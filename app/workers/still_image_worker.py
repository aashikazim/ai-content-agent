"""Offline SDXL in the existing verified isolated CUDA Python; no cloud secrets."""
import fcntl
import json
import os
from pathlib import Path
import sys


def main(task):
    os.umask(0o077);value=json.loads(Path(task).read_text())
    lock=Path(value['gpu_lock']);lock.parent.mkdir(parents=True,exist_ok=True)
    with lock.open('a') as file:
        fcntl.flock(file,fcntl.LOCK_EX)
        import psutil
        if psutil.virtual_memory().available/1024**2<value['policy']['min_available_memory_mb']:raise ValueError('Insufficient host memory reserve')
        import torch
        from diffusers import StableDiffusionXLPipeline
        torch.cuda.set_per_process_memory_fraction(value['policy']['gpu_memory_fraction'])
        pipeline=StableDiffusionXLPipeline.from_pretrained(value['model'],variant='fp16',torch_dtype=torch.float16,use_safetensors=True,local_files_only=True,add_watermarker=False)
        pipeline.to('cuda');pipeline.enable_attention_slicing()
        image=pipeline(value['prompt'],width=value['width'],height=value['height'],num_inference_steps=25,
            generator=torch.Generator(device='cuda').manual_seed(value['seed'])).images[0]
        image.save(value['output'])


if __name__=='__main__':main(sys.argv[1])
