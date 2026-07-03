"""
Main entry point for the document Q&A Assistant.
Adapted from the Kenya ARV Guidelines notebook (cells 48, 63-65, 68).

DEPRECATED: this CLI is bound to the legacy pydantic-ai/Mistral path and is
retained only so older local workflows fail clearly. Use app.api via FastAPI
for the current Groq/Puter runtime.
"""

import os
import asyncio

from dotenv import load_dotenv

load_dotenv()

import ingest
import search_agent
import logs


def _require_mistral_key():
    """Exit with a clear message if MISTRAL_API_KEY is not set."""
    if not os.environ.get("MISTRAL_API_KEY"):
        print(
            "Error: MISTRAL_API_KEY is not set.\n"
            "Set it in your environment or in a .env file in the app directory.\n"
            "On Streamlit Cloud: Manage app → Settings → Secrets → add MISTRAL_API_KEY."
        )
        raise SystemExit(1)


# Configuration
REPO_OWNER = "DataTalksClub"
REPO_NAME = "faq"
PDF_PATH = "Kenya-ARV-Guidelines-2022-Final-1.pdf"  # Same as app.py; must exist in app dir or repo root


def initialize_index():
    """
    Initialize the vector index by ingesting data from PDF.
    From notebook cells 1, 3, 13, 21.

    Returns:
        Tuple of (vectorstore, table)
    """
    print(f"Starting AI Document Assistant for {REPO_OWNER}/{REPO_NAME}")
    print("Initializing data ingestion...")

    index = ingest.index_data(PDF_PATH)
    print("Data indexing completed successfully!")

    return index


def initialize_agent(index):
    """
    Initialize the search agent with the vector index.
    From notebook cell 47.

    Args:
        index: Tuple of (vectorstore, table) from ingest.index_data()

    Returns:
        Configured pydantic-ai agent
    """
    print("Initializing search agent...")
    agent = search_agent.init_agent(index)
    print("Agent initialized successfully!")

    return agent


async def run_interactive():
    """
    Run the interactive Q&A loop.
    From notebook cells 63-65, 68.
    """
    # Initialize components
    index = initialize_index()
    agent = initialize_agent(index)

    print("\nReady to answer your questions!")
    print("Type 'stop' to exit the program.")
    print("Type 'history' to view recent interactions.\n")

    # Main interaction loop
    while True:
        # Get user input (from notebook cells 63-65, 68)
        question = input("Your question: ")

        # Handle special commands
        if question.strip().lower() == "stop":
            print("Goodbye!")
            break

        if question.strip().lower() == "history":
            logs.print_recent_logs(5)
            continue

        if not question.strip():
            continue

        print("Processing your question...")

        try:
            # Run agent asynchronously (from notebook cell 48)
            result = await agent.run(question)

            # Display response
            print(f"\nResponse:\n{result.output}")

            # Log the interaction (from notebook cells 63-65, 68)
            logs.log_interaction_to_file(agent, result.new_messages())

        except Exception as e:
            print(f"\nError processing question: {str(e)}")
            print("Please try again or rephrase your question.")

        print("\n" + "=" * 50 + "\n")


def main():
    """
    Main function to run the document assistant.
    Uses asyncio to run the async interactive loop.
    """
    _require_mistral_key()
    asyncio.run(run_interactive())


if __name__ == "__main__":
    main()
