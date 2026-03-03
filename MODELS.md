# OpenRouter Model Guide

Model recommendations for Aptoflow workflows. All models accessed via OpenRouter using the OpenAI SDK.

## Recommendations by Task Type

| Task Type | Recommended | Fallback |
|-----------|-------------|----------|
| Classification | `google/gemini-2.0-flash-001` | `anthropic/claude-haiku-3.5` |
| Text Generation | `anthropic/claude-sonnet-4` | `openai/gpt-4o` |
| Code Generation | `anthropic/claude-sonnet-4` | `openai/gpt-4o` |
| Summarization | `google/gemini-2.0-flash-001` | `anthropic/claude-haiku-3.5` |
| Data Extraction | `google/gemini-2.0-flash-001` | `openai/gpt-4o-mini` |
| Complex Reasoning | `anthropic/claude-opus-4` | `openai/o3` |
| Multi-step Agentic | `anthropic/claude-sonnet-4` | `openai/gpt-4o` |
| Cheap / High Volume | `google/gemini-2.0-flash-001` | `openai/gpt-4o-mini` |

## Model Details

### google/gemini-2.0-flash-001
- **Context window**: 1M tokens
- **Strengths**: Fast, cheap, great for classification and extraction
- **Cost tier**: $
- **Best for**: High-volume tasks, simple classification, data extraction, summarization

### anthropic/claude-sonnet-4
- **Context window**: 200K tokens
- **Strengths**: Excellent code generation, strong reasoning, reliable tool use
- **Cost tier**: $$
- **Best for**: Code generation, agentic workflows, text generation, complex analysis

### anthropic/claude-opus-4
- **Context window**: 200K tokens
- **Strengths**: Top-tier reasoning, nuanced analysis, complex multi-step tasks
- **Cost tier**: $$$$
- **Best for**: Complex reasoning, research, high-stakes decisions

### anthropic/claude-haiku-3.5
- **Context window**: 200K tokens
- **Strengths**: Fast, cheap, good for simple tasks
- **Cost tier**: $
- **Best for**: Fallback for classification, summarization, simple extraction

### openai/gpt-4o
- **Context window**: 128K tokens
- **Strengths**: Strong all-rounder, good tool use
- **Cost tier**: $$
- **Best for**: Fallback for code generation, text generation, agentic workflows

### openai/gpt-4o-mini
- **Context window**: 128K tokens
- **Strengths**: Very cheap, decent quality
- **Cost tier**: $
- **Best for**: Fallback for extraction, high-volume tasks

### openai/o3
- **Context window**: 200K tokens
- **Strengths**: Advanced reasoning, chain-of-thought
- **Cost tier**: $$$
- **Best for**: Fallback for complex reasoning tasks

## Selection Guidelines

1. **Start with the recommended model** for your task type
2. **Use fallback** if the recommended model has issues (rate limits, cost concerns)
3. **Prefer cheaper models** for high-volume or simple tasks
4. **Use Claude Opus** only when reasoning quality justifies the cost
5. **Test with your actual data** — benchmarks don't always reflect real-world performance
6. **Set cost budgets** in your workflow's safety configuration
