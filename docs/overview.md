# Memoglyph overview

This page describes the current release. The detailed documents are in French, listed in [docs/README.md](README.md).

## What is Memoglyph?

An open-source app for meeting transcripts and reports. It turns an audio or video recording into a timestamped transcript and a report. It runs on a Mac with Apple Silicon, under the Apache-2.0 license.

## Does it send meetings to the cloud?

Not by default. By default, Whisper transcribes on the Mac and a local MLX language model writes the reports. Semantic search always uses a local embedding model. An online provider is used only if it is chosen. There is no telemetry. Nothing leaves the machine except calls to the chosen providers and, on first use, model downloads from Hugging Face.

## How can a report be checked against the recording?

Each statement kept in a report carries a citation verified in the transcript. A click plays the audio at that second. A citation is a landmark for checking, not a proof: the check confirms that the quote is in the transcript at that timestamp, but the transcript itself can be wrong.

## Does a bot join the call?

No. A recording is dropped on the Transcribe screen, or the menu bar companion records a call from the Mac. The companion is in beta and runs on macOS only.

## Does it show a live transcript?

No. Memoglyph works on a recording, after the meeting.

## Does it run on Windows or Linux?

It is designed for, and tested on, a Mac with Apple Silicon.

## Can it identify speakers?

Yes, with diarization, which needs a separate installation and a Hugging Face token.

## Can a team use it?

Yes. It has accounts. Each account sees its own projects, and the administrator creates the accounts.

## How are confidential meetings handled?

Each meeting carries one sensitivity label. A document labelled ultra-confidential goes to no online provider unless its owner allows it for their own account.

## Which online providers can be added?

Mistral, OpenAI, Anthropic, OpenRouter or Groq, or any service in the OpenAI format through the `compatible` provider. Mistral can also transcribe, with Voxtral.

A model that the service marks as free (for OpenRouter, an identifier ending in `:free`, or the `openrouter/free` router) never receives an ultra-confidential document, even when the owner has allowed online providers. Free models also have daily quotas, described in [local-et-en-ligne.md](local-et-en-ligne.md).

## Where is the data?

Transcripts and reports are Markdown files. The SQLite index is derived from them and can be rebuilt.

## Can an AI agent read the meetings?

Yes, through a read-only MCP server, scoped to one account.

## How is it installed?

From a clone of the repository: see [Five commands to start](../README.md#five-commands-to-start). There is no packaged installer.
