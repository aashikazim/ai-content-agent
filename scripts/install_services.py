"""Render user service files; installing/enabling services remains explicit."""
import argparse
from pathlib import Path


def render(root):
    root=Path(root).absolute()
    if any(v in str(root) for v in ('\n','\r','%')):raise ValueError('Unsupported service working-directory characters')
    # systemd ExecStart requires quoting paths containing spaces.
    for template in sorted((root/'deploy/systemd').glob('*.service')):
        text=template.read_text().replace('@ROOT@',str(root))
        if ' ' in str(root):
            text=text.replace('WorkingDirectory='+str(root),'WorkingDirectory="'+str(root)+'"').replace('ExecStart='+str(root)+'/.venv/bin/python','ExecStart="'+str(root)+'/.venv/bin/python"')
        yield template.name,text


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--install',action='store_true')
    parser.add_argument('--output',default='data/service-preview');args=parser.parse_args()
    root=Path(__file__).absolute().parents[1]
    target=Path.home()/'.config/systemd/user' if args.install else Path(args.output)
    target.mkdir(parents=True,exist_ok=True)
    for name,text in render(root):
        path=target/name;path.write_text(text);path.chmod(0o600);print(path)
    print('No services were started. Review files, then use systemctl --user daemon-reload and enable --now explicitly.')


if __name__=='__main__':main()
