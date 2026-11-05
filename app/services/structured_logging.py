"""Optional structured operational logs with credential/query redaction."""
import datetime
import json
import logging
import os
import re


class RedactedJSONFormatter(logging.Formatter):
    def format(self,record):
        message=record.getMessage()
        for name,value in os.environ.items():
            if len(value)>=8 and any(token in name.upper() for token in ('SECRET','TOKEN','PASSWORD','API_KEY')):
                message=message.replace(value,'[redacted]')
        message=re.sub(r'(?i)(bearer\s+)[A-Za-z0-9_.~+/=-]+',r'\1[redacted]',message)
        message=re.sub(r'(https?://[^\s?]+)\?[^\s]+',r'\1?[redacted]',message)
        value={'time':datetime.datetime.now(datetime.timezone.utc).isoformat(),'level':record.levelname,'logger':record.name,'message':message[:4000]}
        if record.exc_info:value['exception_type']=record.exc_info[0].__name__
        return json.dumps(value,ensure_ascii=False)


def configure():
    if os.getenv('LOG_FORMAT_JSON','false').lower()!='true':return
    if not logging.getLogger().handlers:logging.basicConfig(level=logging.INFO)
    formatter=RedactedJSONFormatter()
    for logger in (logging.getLogger(),logging.getLogger('uvicorn'),logging.getLogger('uvicorn.error'),logging.getLogger('uvicorn.access')):
        for handler in logger.handlers:handler.setFormatter(formatter)
