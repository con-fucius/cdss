import unittest
from pathlib import Path
from unittest.mock import patch


class PackagingContractTests(unittest.TestCase):
    def test_root_env_example_contains_groq_and_serving_defaults(self):
        env_example = Path(".env.example").read_text(encoding="utf-8")

        self.assertIn("QUERY_LLM_PROVIDER=groq", env_example)
        self.assertIn("GROQ_API_KEY=", env_example)
        self.assertIn("QUERY_LLM_MODEL=qwen/qwen3-32b", env_example)
        self.assertIn("GROQ_REASONING_FORMAT=hidden", env_example)
        self.assertIn("CDSS_CHAT_PAGEINDEX_MODE=off", env_example)
        self.assertIn("CDSS_PAGEINDEX_CHAT_TIMEOUT_SECONDS=3", env_example)
        self.assertIn("PUTER_AUTH_TOKEN=", env_example)
        self.assertIn("PUTER_MODEL=openai/gpt-4o-mini", env_example)
        self.assertIn("PUTER_OPENAI_BASE_URL=https://api.puter.com/puterai/openai/v1", env_example)
        self.assertIn("DATABASE_URL=postgresql+asyncpg://cdss:cdss@localhost:5432/cdss", env_example)
        self.assertNotIn("FASTEMBED_CACHE_DIR", env_example)
        self.assertNotIn("CDSS_RUN_MIGRATIONS_ON_STARTUP", env_example)

    def test_provider_uses_stable_provider_env_names(self):
        from app.providers import get_llm_model, provider_chat_endpoint, provider_models_url

        with patch.dict(
            "os.environ",
            {
                "QUERY_LLM_PROVIDER": "groq",
                "QUERY_LLM_MODEL": "qwen/qwen3-32b",
            },
            clear=False,
        ):
            self.assertEqual(get_llm_model(), "qwen/qwen3-32b")
            self.assertEqual(
                provider_chat_endpoint("groq"),
                "https://api.groq.com/openai/v1/chat/completions",
            )
            self.assertEqual(
                provider_models_url("groq"),
                "https://api.groq.com/openai/v1/models",
            )

        with patch.dict(
            "os.environ",
            {
                "QUERY_LLM_PROVIDER": "puter",
                "PUTER_MODEL": "openai/gpt-5-nano",
                "PUTER_OPENAI_BASE_URL": "https://example.test/v1/",
            },
            clear=False,
        ):
            self.assertEqual(get_llm_model(), "openai/gpt-5-nano")
            self.assertEqual(
                provider_chat_endpoint("puter"),
                "https://example.test/v1/chat/completions",
            )
            self.assertEqual(
                provider_models_url("puter"),
                None,
            )

    def test_docker_compose_has_migration_and_frontend_proxy_shape(self):
        compose = Path("docker-compose.yml").read_text(encoding="utf-8")
        nginx = Path("frontend/nginx.conf").read_text(encoding="utf-8")
        dockerignore = Path(".dockerignore").read_text(encoding="utf-8")
        gitignore = Path(".gitignore").read_text(encoding="utf-8")

        self.assertIn("migrate:", compose)
        self.assertIn("python -m app.migrations", compose)
        self.assertIn("python -m scripts.seed_evidence", compose)
        self.assertIn("condition: service_completed_successfully", compose)
        self.assertIn("frontend:", compose)
        self.assertIn("./app/lancedb:/app/app/lancedb", compose)
        self.assertNotIn("./app/data:/app/app/data", compose)
        self.assertIn("proxy_pass http://backend:8000", nginx)
        self.assertIn("try_files $uri $uri/ /index.html", nginx)
        self.assertIn("app/lancedb", dockerignore)
        self.assertIn("app/data/fastembed_cache", dockerignore)
        self.assertIn("!app/lancedb/**", gitignore)

    def test_local_development_has_three_command_service_shape(self):
        services = Path("scripts/dev-services.ps1").read_text(encoding="utf-8")
        backend = Path("scripts/dev-backend.ps1").read_text(encoding="utf-8")
        frontend = Path("scripts/dev-frontend.ps1").read_text(encoding="utf-8")

        self.assertIn("docker compose up -d postgres", services)
        self.assertIn("python -m app.migrations", services)
        self.assertIn("python -m scripts.seed_evidence", services)
        self.assertIn('"app.api:app", "--host", "127.0.0.1", "--port", "8000"', backend)
        self.assertIn("CDSS_BACKEND_RELOAD", backend)
        self.assertIn("uv run uvicorn @UvicornArgs", backend)
        self.assertIn("pnpm dev --host 127.0.0.1 --port 5173", frontend)

    def test_api_startup_does_not_run_migrations(self):
        api = Path("app/api.py").read_text(encoding="utf-8")

        self.assertNotIn("should_run_dev_migrations", api)
        self.assertNotIn("run_migrations", api)

    def test_fastembed_cache_repair_is_not_part_of_current_setup(self):
        current_scripts = [
            Path("scripts/build_pageindex.py"),
            Path("scripts/index_malaria_minimal.py"),
            Path("scripts/index_remaining_5.py"),
            Path("scripts/phase01_smoke.py"),
        ]
        for script in current_scripts:
            text = script.read_text(encoding="utf-8")
            self.assertNotIn("repair_cache", text, str(script))
            self.assertNotIn("FASTEMBED_CACHE_DIR", text, str(script))


if __name__ == "__main__":
    unittest.main()
