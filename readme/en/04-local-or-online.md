## Local or online, as chosen

By default, everything runs on the Mac: Whisper for transcription, and a local MLX language model for reports and the assistant. Semantic search also uses a local embedding model by default.

An online model can be added when needed: Mistral, OpenAI or Anthropic, or any service in the OpenAI format, such as Groq, through the `compatible` provider. Mistral can also transcribe, with Voxtral.

Both can be combined according to the sensitivity of each meeting. A document labelled ultra-confidential goes to no online provider unless its owner allows it for their account. Details, in French: [docs/local-et-en-ligne.md](docs/local-et-en-ligne.md).

There is no telemetry. Usage is recorded locally, and nothing leaves the machine except calls to the providers chosen and, on first use, model downloads from Hugging Face.
