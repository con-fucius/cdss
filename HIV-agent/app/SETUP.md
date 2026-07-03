# HIV Guidelines Assistant - Setup Instructions

## Virtual Environment Setup

### 1. Navigate to project directory
```bash
cd D:\Projects\CDSS\HIV-agent\app
```

### 2. Create virtual environment
```bash
python -m venv venv
```

### 3. Activate virtual environment

**Windows (Command Prompt):**
```bash
venv\Scripts\activate
```

**Windows (PowerShell):**
```powershell
venv\Scripts\Activate.ps1
```

**macOS/Linux:**
```bash
source venv/bin/activate
```

### 4. Install dependencies
```bash
pip install -r requirements.txt
```

### 5. Set up environment variables

Create a `.env` file in the `app/` directory:
```bash
echo MISTRAL_API_KEY=your-mistral-api-key-here > .env
```

Or manually create `.env` with:
```
MISTRAL_API_KEY=your-mistral-api-key-here
```

### 6. Run the application
```bash
streamlit run app.py
```

### 7. Deactivate when done
```bash
deactivate
```

---

## Complete One-Command Setup (Windows)

```bash
cd D:\Projects\CDSS\HIV-agent\app && python -m venv venv && venv\Scripts\activate && pip install -r requirements.txt && echo MISTRAL_API_KEY=your-mistral-api-key-here > .env
```

Then run:
```bash
streamlit run app.py
```

---

## What was changed for Mistral support:

1. **search_agent.py** - Default model changed to `mistral:mistral-small-latest`
2. **app.py** - All `OPENAI_API_KEY` references changed to `MISTRAL_API_KEY`
3. **main.py** - All `OPENAI_API_KEY` references changed to `MISTRAL_API_KEY`
4. **requirements.txt** - Already includes `mistralai` and `pydantic-ai` with Mistral support

The `pydantic-ai` library automatically routes to Mistral when the model string starts with `mistral:` and reads `MISTRAL_API_KEY` from environment.
