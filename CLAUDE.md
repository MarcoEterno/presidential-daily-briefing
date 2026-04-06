# Presidential Daily Briefing

Automated geopolitical intelligence briefing system. Runs daily to collect news, analyze with Claude API, and distribute via email + GitHub Pages.

## Running

```bash
pip install -r requirements.txt
python -m src.main --dry-run          # Full pipeline, no distribution
python -m src.main --from-stage=rank  # Resume from ranking stage
python -m src.main --verbose          # Verbose logging
```

## Architecture

Pipeline: Collectors -> Ranker -> Researcher -> Report Generator -> Distributor

- `src/collectors/` — GDELT API, think tank RSS feeds, NewsAPI (optional)
- `src/analysis/ranker.py` — Claude ranks headlines by geopolitical importance
- `src/analysis/researcher.py` — Claude synthesizes deep context per story
- `src/report/generator.py` — Claude compiles final briefing + Jinja2 HTML
- `src/distribution/` — Resend email + GitHub Pages publishing
- `src/config/settings.py` — All config via environment variables
- `src/models.py` — Pydantic data models for pipeline stages

## Environment Variables

Required: `ANTHROPIC_API_KEY`
Optional: `NEWSAPI_KEY`, `RESEND_API_KEY`, `RESEND_FROM_EMAIL`, `GITHUB_TOKEN`

## Cloud Deployment

Primary: GitHub Actions (`.github/workflows/daily_briefing.yml`)
Alternative: Docker (`Dockerfile`) for any cloud provider
