"""Owner-created, conservative quote reservations. No implicit paid fallback."""
from datetime import datetime
from decimal import Decimal,InvalidOperation
import json
from uuid import uuid4
from app.automation.clock import local_day,utc_now
from app.automation.events import EventLog
from app.automation.idempotency import canonical_json


def money(value):
    try:amount=Decimal(str(value))
    except InvalidOperation:raise ValueError('Invalid monetary amount') from None
    if not amount.is_finite() or amount<=0:raise ValueError('Caps and paid quotes must be positive finite amounts')
    return amount


class StandingAuthorizations:
    def __init__(self,storage,settings):self.storage,self.settings=storage,settings

    def create(self,providers,daily,total,expires_at,*,actor,confirmed=False):
        if confirmed is not True or not actor:raise ValueError('Owner confirmation is required')
        if not providers or len(set(providers))!=len(providers) or not set(providers)<={'openai','anthropic','google','runway'}:raise ValueError('Choose supported paid providers')
        daily,total=money(daily),money(total)
        if daily>total:raise ValueError('Daily cap must not exceed total cap')
        expiry=datetime.fromisoformat(expires_at)
        if expiry.tzinfo is None or expiry<=utc_now(self.storage.clock):raise ValueError('Expiry must be a future timezone-aware instant')
        identifier=str(uuid4());now=self.storage.timestamp()
        with self.storage.connect() as c:c.execute('''INSERT INTO standing_authorizations
            (authorization_id,providers_json,max_daily_usd,max_total_usd,expires_at,created_by,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?)''',
            (identifier,canonical_json(providers),str(daily),str(total),expiry.isoformat(),actor,now,now))
        EventLog(self.storage).append('user','authorization.created','authorization',identifier,{'providers':providers,'daily_cap':str(daily),'total_cap':str(total),'expires_at':expiry.isoformat(),'actor':actor})
        return identifier

    def list(self):
        with self.storage.connect() as c:return [dict(r) for r in c.execute('SELECT * FROM standing_authorizations ORDER BY created_at DESC')]

    def revoke(self,identifier,*,actor,confirmed=False):
        if confirmed is not True:raise ValueError('Confirm revocation')
        with self.storage.connect() as c:
            if c.execute("UPDATE standing_authorizations SET status='revoked',updated_at=? WHERE authorization_id=?",(self.storage.timestamp(),identifier)).rowcount!=1:raise ValueError('Unknown authorization')
        EventLog(self.storage).append('user','authorization.revoked','authorization',identifier,{'actor':actor})

    def reserve(self,identifier,kind,quote_id,provider,actor):
        if kind not in {'script','video'}:raise ValueError('Unknown quote kind')
        day=local_day(self.storage.clock,self.settings.config.timezone).isoformat();now=self.storage.timestamp()
        table='script_generation_quotes' if kind=='script' else 'video_generation_quotes'
        cost='maximum_cost_usd' if kind=='script' else 'estimate_usd'
        with self.storage.connect() as c:
            c.execute('BEGIN IMMEDIATE')
            prior=c.execute('SELECT * FROM automation_spend_reservations WHERE quote_kind=? AND quote_id=?',(kind,quote_id)).fetchone()
            if prior:raise ValueError('Quote already reserved; inspect saved result, never resubmit')
            auth=c.execute('SELECT * FROM standing_authorizations WHERE authorization_id=?',(identifier,)).fetchone()
            if not auth or auth['status']!='active' or datetime.fromisoformat(auth['expires_at'])<=utc_now(self.storage.clock):raise ValueError('Standing authorization is missing, revoked or expired')
            if provider not in json.loads(auth['providers_json']) or actor!=f'automation:standing:{identifier}':raise ValueError('Provider or quote actor is not authorized')
            quote=c.execute(f'SELECT * FROM {table} WHERE quote_id=?',(quote_id,)).fetchone()
            if not quote or quote['actor']!=actor or quote['consumed_at'] is not None or quote['expires_at']<=utc_now(self.storage.clock).timestamp():raise ValueError('Valid unused actor-bound quote is required')
            amount=money(quote[cost]);spent=Decimal(auth['consumed_total_usd'])
            rows=c.execute('SELECT amount_usd,authorization_id FROM automation_spend_reservations WHERE spend_day=?',(day,)).fetchall()
            global_day=sum((Decimal(r[0]) for r in rows),Decimal(0));daily=sum((Decimal(r[0]) for r in rows if r[1]==identifier),Decimal(0))
            if amount+daily>Decimal(auth['max_daily_usd']) or spent+amount>Decimal(auth['max_total_usd']) or amount+global_day>self.settings.daily_budget_usd:raise ValueError('Standing or global automation budget exceeded; use local or skip')
            reservation=str(uuid4())
            c.execute('''INSERT INTO automation_spend_reservations(reservation_id,authorization_id,quote_kind,quote_id,provider,amount_usd,spend_day,status,created_at,updated_at)
                VALUES (?,?,?,?,?,?,?,'reserved',?,?)''',(reservation,identifier,kind,quote_id,provider,str(amount),day,now,now))
            c.execute('UPDATE standing_authorizations SET consumed_total_usd=?,updated_at=? WHERE authorization_id=?',(str(spent+amount),now,identifier))
        EventLog(self.storage).append('director','authorization.reserved','authorization',identifier,{'quote_id':quote_id,'kind':kind,'amount_usd':str(amount),'reservation_id':reservation})
        return reservation

    def _execute(self,identifier,kind,quote_id,provider,operation):
        actor=f'automation:standing:{identifier}'
        reservation=self.reserve(identifier,kind,quote_id,provider,actor)
        try:
            result=operation(actor)
            status='submitted'
        except Exception:
            with self.storage.connect() as c:c.execute("UPDATE automation_spend_reservations SET status='needs_reconciliation',updated_at=? WHERE reservation_id=?",(self.storage.timestamp(),reservation))
            raise
        with self.storage.connect() as c:c.execute('UPDATE automation_spend_reservations SET status=?,result_json=?,updated_at=? WHERE reservation_id=?',(status,canonical_json({'id':result if isinstance(result,str) else result.get('production_id')}),self.storage.timestamp(),reservation))
        return result

    def submit_script(self,identifier,quote_id,request,service):
        provider=request['configuration']['script_provider']
        return self._execute(identifier,'script',quote_id,provider,lambda actor:service.submit(request,actor,quote_id=quote_id,confirmed=True))

    def create_video(self,identifier,quote_id,service):
        with self.storage.connect() as c:row=c.execute('SELECT request_json FROM video_generation_quotes WHERE quote_id=?',(quote_id,)).fetchone()
        if not row:raise ValueError('Quote not found')
        provider=json.loads(row[0])['configuration']['video_provider']
        return self._execute(identifier,'video',quote_id,provider,lambda actor:service.create(quote_id,actor,True))
