"""Anjo CLI — the main conversation REPL."""

from __future__ import annotations

import os
import threading
import uuid
from datetime import datetime

import typer
from dotenv import load_dotenv

load_dotenv()

app = typer.Typer(add_completion=False)


def _validate_env() -> None:
    pass  # Running locally via Ollama — no API key required


@app.command()
def chat(
    user_id: str = typer.Option(
        None,
        "--user",
        "-u",
        help="User ID (defaults to ANJO_USER_ID env var or 'user_default')",
    ),
) -> None:
    """Start a conversation with Anjo."""
    _validate_env()

    # Lazy imports after env validation so startup errors are clean
    from anjo.core.self_core import SelfCore
    from anjo.graph.conversation_graph import conversation_graph

    effective_user_id = user_id or os.environ.get("ANJO_USER_ID", "user_default")
    session_id = f"session_{datetime.now().strftime('%Y%m%dT%H%M%S')}_{uuid.uuid4().hex[:6]}"

    core = SelfCore.load(effective_user_id)

    # Process any transcripts that failed to reflect last session
    from anjo.core.transcript_queue import process_all_pending

    pending_count = process_all_pending()
    if pending_count:
        typer.echo(f"[Caught up on {pending_count} saved conversation(s).]")

    typer.echo("\nAnjo\n" + "─" * 40)

    state: dict = {
        "user_message": "",
        "conversation_history": [],
        "self_core": core.model_dump(),
        "should_retrieve": False,
        "retrieved_memories": [],
        "assistant_response": "",
        "active_emotions": {},
        "occ_carry": {},
        "intent": "",
        "user_id": effective_user_id,
    }

    try:
        while True:
            try:
                user_input = input("\nYou: ").strip()
            except (EOFError, KeyboardInterrupt):
                raise KeyboardInterrupt

            if not user_input:
                continue

            if user_input.lower() in ("exit", "quit", "bye"):
                raise KeyboardInterrupt

            state["user_message"] = user_input
            state["retrieved_memories"] = []
            state["assistant_response"] = ""

            try:
                result = conversation_graph.invoke(state)
            except Exception as e:
                import openai

                if isinstance(e, openai.APIConnectionError):
                    typer.echo(f"\nAnjo: [connection issue — is Ollama running? {e}]")
                    continue
                raise

            state["conversation_history"] = result["conversation_history"]
            state["should_retrieve"] = result.get("should_retrieve", False)

            typer.echo(f"\nAnjo: {result['assistant_response']}")

    except KeyboardInterrupt:
        transcript = state.get("conversation_history", [])
        if not transcript:
            typer.echo("\n")
            return

        # Save transcript immediately — no LLM, always succeeds
        from anjo.core.transcript_queue import delete_pending, save_pending

        pending_path = save_pending(transcript, effective_user_id, session_id)
        typer.echo("\n\n[Reflecting on this conversation...]")

        def _reflect() -> None:
            from anjo.reflection.engine import run_reflection

            try:
                run_reflection(
                    transcript=transcript,
                    core=core,
                    user_id=effective_user_id,
                    session_id=session_id,
                )
                delete_pending(pending_path)
                typer.echo("[Done. Anjo remembers.]")
            except Exception as e:
                typer.echo(f"[Reflection failed — saved for next session. ({e})]")

        t = threading.Thread(target=_reflect, daemon=False)
        t.start()
        t.join()
        typer.echo("")


if __name__ == "__main__":
    app()
