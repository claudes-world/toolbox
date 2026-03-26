# toolbox

Handmade CLI tools by a human-AI duo who share a Linux box.

## Tools

### transcribe

Transcribe audio files using OpenAI's speech-to-text API. Ships with a technical glossary that primes the model to correctly handle terms like `git`, `Docker`, `kubectl`, etc.

```
transcribe audio.ogg              # mini model + glossary (default)
transcribe --full audio.ogg       # full model + glossary
transcribe --no-glossary audio.ogg
```

Requires `OPENAI_API_KEY` in the environment and a Python venv with the `openai` package.

## Setup

Each tool lives in its own directory. Symlink the executable into `~/bin/`:

```
ln -sf ~/code/toolbox/transcribe/transcribe ~/bin/transcribe
```

## License

MIT
