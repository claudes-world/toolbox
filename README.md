# toolbox

Handmade CLI tools by a human-AI duo who share a Linux box.

## Tools

### transcribe

Transcribe audio files using OpenAI's speech-to-text API. Ships with a technical glossary that primes the model to correctly handle terms like `git`, `Docker`, `kubectl`, etc. Auto-converts unsupported formats (like Telegram's `.oga`) via ffmpeg.

```
transcribe audio.ogg              # mini model + glossary (default)
transcribe --full audio.ogg       # full model + glossary
transcribe --no-glossary audio.ogg
```

Requires `OPENAI_API_KEY` in `~/.secrets/openai.env`.

### speak

Text-to-speech using the ElevenLabs API. Outputs ogg opus by default (renders as voice bubbles in Telegram). Multiple voice presets available.

```
speak "Hello, how are you?"              # default voice (eric), ogg output
speak --voice brian "Deep and resonant."  # different voice
speak --out reply.ogg "Saved to file."   # explicit output path
echo "Piped input" | speak --mp3         # mp3 instead of ogg
```

Available voices: `eric`, `river`, `george`, `daniel`, `brian`, `alice`, `bella`

Requires `ELEVEN_API_KEY` in `~/.secrets/elevenlabs.env`.

## Setup

Each tool lives in its own directory. Symlink the executable into `~/bin/`:

```
ln -sf ~/code/toolbox/transcribe/transcribe ~/bin/transcribe
ln -sf ~/code/toolbox/speak/speak ~/bin/speak
```

Both tools use the Python venv at `~/venvs/transcribe/` (shebang points directly to it).

## License

MIT
