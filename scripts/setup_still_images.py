"""Download a pinned optional SDXL checkpoint; never alter existing model environments."""
import argparse
import hashlib
import json
from pathlib import Path

REPO='stabilityai/stable-diffusion-xl-base-1.0'


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--root',default='data/still_image_runtime')
    parser.add_argument('--download-model',action='store_true');args=parser.parse_args()
    if not args.download_model:
        print('Review https://huggingface.co/'+REPO+' and its OpenRAIL++ license, then pass --download-model explicitly.');return
    from huggingface_hub import HfApi,snapshot_download
    root=Path(args.root).absolute();root.mkdir(parents=True,exist_ok=True,mode=0o700)
    if (root/'checkpoint.json').exists():print('Existing pinned checkpoint retained.');return
    revision=HfApi().model_info(REPO).sha
    directory=root/'model'
    snapshot_download(REPO,revision=revision,local_dir=directory,
        allow_patterns=['model_index.json','*.json','*.txt','*.model','*.fp16.safetensors','LICENSE*','README.md'])
    files={}
    for path in directory.rglob('*'):
        if path.is_file() and '.cache' not in path.parts:
            with path.open('rb') as file:digest=hashlib.file_digest(file,'sha256').hexdigest()
            files[str(path.relative_to(directory))]={'size':path.stat().st_size,'sha256':digest}
    value={'repo':REPO,'revision':revision,'variant':'fp16','license':'CreativeML Open RAIL++-M',
           'license_url':'https://huggingface.co/'+REPO+'/blob/'+revision+'/LICENSE.md','files':files}
    (root/'checkpoint.json').write_text(json.dumps(value));(root/'checkpoint.json').chmod(0o600)
    print('Pinned standalone SDXL checkpoint:',revision)


if __name__=='__main__':main()
