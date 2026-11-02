
# AI Content Creator Agent

A Python-based AI agent for discovering trending content,
generating original short-form videos, and publishing
approved content to YouTube Shorts and Facebook Reels.

## Target Environment

- NVIDIA DGX Spark (ARM64)
- Python 3.11+
- Local AI models
- YouTube Data API
- SQLite (Day 1)

## Planned Features

- [x] YouTube popular-video discovery
- [x] Category filtering
- [x] Video ranking
- [x] SQLite persistence
- [x] Unit tests
- [ ] Topic clustering and research
- [ ] Local LLM script generation
- [ ] Local video generation
- [ ] Voice generation and subtitles
- [ ] YouTube publishing
- [ ] Facebook Reels publishing
- [ ] Performance analytics

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

Add your YouTube API key to `.env`.

## Usage

```bash
python main.py --region US --limit 20
python main.py --category science
python main.py --short-form --limit 50
```

## Testing

```bash
python -m pytest tests/ -v
```

## Status

Day 1: Trend Discovery Agent.
