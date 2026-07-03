# UMLS Clinical Decision Support System (CDSS)

A clinical decision support system powered by UMLS terminology, multiple LLM providers, and advanced RAG (Retrieval-Augmented Generation) techniques.

## Features

- **UMLS Integration**: Search and leverage UMLS terminology and semantic relationships
- **Multiple LLM Support**: GPT-4, Llama 3, Med42, Falcon
- **Advanced RAG**: Three RAG implementations (BM25+Embeddings, PGVector, Hybrid)
- **Evaluation Framework**: Comprehensive metrics and LLM-based evaluation
- **Experiment Tracking**: Systematic experimentation and performance tracking

## Project Structure

```
umls-cdss/
├── api/                    # FastAPI application
├── experiments/            # Experiment code and notebooks
├── etl/                    # Data processing scripts
├── infrastructure/         # Docker, K8s, Terraform configs
└── tests/                  # Test suite
```

## Quick Start

### Prerequisites

- Python 3.11+
- PostgreSQL 16+ with pgvector extension
- Redis (for caching)
- UMLS API key (for terminology access)
- LLM API keys (OpenAI, etc.)

### Quick Setup

**For detailed setup instructions, see [SETUP.md](SETUP.md)**

**Prerequisites**: Python 3.11+ and Poetry (see [Poetry installation](https://python-poetry.org/docs/#installation))

1. **Install dependencies:**
```bash
poetry install
# Or for production only: poetry install --without dev
```

2. **Set up environment variables:**
```bash
# Create .env file with your API keys (see SETUP.md for template)
```

3. **Initialize database:**
```bash
make db-init
# Or manually run SQL scripts in etl/sql/
```

4. **Run the API:**
```bash
make run
# Or: poetry run uvicorn api.main:app --reload
```

### Docker Compose (All-in-One)

```bash
cd infrastructure/docker
docker-compose up -d
```

## API Endpoints

### Health Check
- `GET /health/` - Health status
- `GET /health/ready` - Readiness check
- `GET /health/live` - Liveness check

### Terminology
- `POST /api/v1/terminology/search` - Search UMLS concepts
- `GET /api/v1/terminology/concept/{cui}` - Get concept details
- `GET /api/v1/terminology/semantic-types` - List semantic types

### Inference
- `POST /api/v1/inference/triage` - Stable inference endpoint (RAG v1)
- `POST /api/v2/inference/triage` - Experimental endpoint (RAG v2/v3)

### Admin
- `GET /api/v1/admin/experiments` - List experiments
- `GET /api/v1/admin/experiments/{id}` - Get experiment details
- `POST /api/v1/admin/experiments/{id}/run` - Run experiment

## Configuration

Key configuration options in `api/config.py`:

- `DATABASE_URL`: PostgreSQL connection string
- `REDIS_URL`: Redis connection string
- `OPENAI_API_KEY`: OpenAI API key
- `UMLS_API_KEY`: UMLS API key
- `EMBEDDING_MODEL`: Sentence transformer model
- `VECTOR_DIMENSION`: Embedding dimension

## Development

### Running Tests

```bash
pytest tests/
```

### Running Experiments

```bash
# Navigate to experiment directory
cd experiments/llm/v1_openai

# Run experiment script
python run_experiment.py
```

## ETL Process

1. **Download UMLS data** (requires license):
```bash
python etl/download_umls.py
```

2. **Transform data**:
```bash
python etl/transform_umls.py
```

3. **Load to database**:
```bash
python etl/load_postgres.py
```

## Architecture

### RAG Versions

- **v1**: BM25 + Simple embeddings (fast, good for keyword matching)
- **v2**: PGVector + Clinical context window (dense retrieval)
- **v3**: Hybrid (graph + dense + sparse)

### LLM Models

- **OpenAI**: GPT-4, GPT-3.5-turbo
- **Llama**: Llama 3, Llama 3.1
- **Med42**: Clinical LLM
- **Falcon**: Falcon models

## License

[Specify your license]

## Contributing

[Contributing guidelines]

## Contact

[Contact information]

