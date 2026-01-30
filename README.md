# Portfolio Health Report

Automated analysis of project email threads for QBR preparation. Reads conversations, identifies unresolved issues and emerging risks, outputs a prioritized report.

## Quick Start

```bash
# Prerequisites: Ollama running with the model
ollama run hf.co/unsloth/Qwen3-1.7B-GGUF:UD-Q8_K_XL

# Install dependencies
pip install ollama pydantic

# Run analysis
python automated_portfolio_health_report.py
```

Outputs:

- `portfolio_health_report.json`: structured data for downstream tools
- `portfolio_health_report.md`: human-readable portfolio health report sorted by severity

## Model Choice

**Model**: Unsloth's Qwen3-1.7B language model (8-bit quantized GGUF)

**Why this model**:

1. **Runs locally**: no API costs, no data leaving the machine. Project emails often contain sensitive information, local inference avoids compliance issues.

2. **Small but capable**: 1.7B parameters is enough for structured extraction from conversational text. The task is finding specific patterns (questions without answers, blockers without resolutions) and outputting JSON.

3. **Qwen3 architecture**: supports long context (32768 tokens), handles multi-turn conversations well. Email threads of the dataset fit comfortably.

4. **Quantized for efficiency**: Unsloth's 8-bit quantization cuts memory usage significantly. It runs even on GPUs with limited VRAM, such as mine, which has 4GB of VRAM.

5. **Structured output support**: The model fills in the Pydantic schema fields rather than free-forming a response.

**Tradeoffs**:

- Less nuanced than LLMs with more hyperparameters on ambiguous cases
- May miss subtle implications that a larger model would catch
- Smaller token context window

For this proof-of-concept, speed and cost matter more than catching edge cases. Production could add a larger model as a second-pass filter for high-stakes threads.

## Configuration

```bash
# Analyze only first 5 threads
python automated_portfolio_health_report.py --limit 5

# Use different model
python automated_portfolio_health_report.py --model llama3:8b

# Custom input/output paths
python automated_portfolio_health_report.py \
  --input-dir ./my_emails \
  --output-json ./reports/report.json \
  --output-md ./reports/report.md

# Lower temperature for more conservative flagging
python automated_portfolio_health_report.py --temperature 0.1
```

## Files

| File | Purpose |
|------|---------|
| `automated_portfolio_health_report.py` | Core analysis engine |
| `Blueprint.md` | System design, prompt engineering rationale, production considerations |
| `AI_Developer/` | Sample email threads for testing |

## How It Works

1. Reads all `email*.txt` files from input directory
2. Sends each thread to the LLM with a structured prompt
3. LLM returns JSON with identified flags (unresolved items, emerging risks)
4. Validates response against Pydantic schema
5. Aggregates results into final report sorted by severity

The prompt instructs the model to:

- Only flag genuinely unresolved issues
- Skip anything resolved later in the thread
- Include evidence quotes from the source text
- Assign severity based on business impact

See [Blueprint.md](Blueprint.md) for the full prompt and design rationale.

## Limitations

- Single-pass analysis; no cross-thread correlation
- Relies on email threading being correct in the source files
- Model may miss issues expressed through heavy sarcasm or cultural idioms