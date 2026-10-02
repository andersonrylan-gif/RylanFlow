"""MCP server exposing RylanFlow's dictations and meeting transcripts to other AI tools (Claude
Desktop, etc.), read-only, over the same local SQLite database the menu-bar app writes to.

Run standalone with `rylanflow mcp` -- it opens its own Store and doesn't need the menu-bar app
to be running. Everything here stays local: this just lets an MCP client read what's already on
the user's Mac, the same way Wispr Flow's Notetaker offers "MCP access to meeting notes".
"""

from rylanflow.store import Store

INSTRUCTIONS = (
    "Access to the user's local dictation history and meeting transcripts, captured and "
    "transcribed entirely on their Mac. Use list_dictations / list_meetings to find things, "
    "get_meeting_transcript for a meeting's full speaker-labeled transcript."
)


def build_server(store: Store):
    from mcp.server.mcpserver import MCPServer

    server = MCPServer("rylanflow", instructions=INSTRUCTIONS)

    @server.tool()
    def list_dictations(query: str | None = None, limit: int = 50) -> list[dict]:
        """List the user's recent dictations, newest first. `query` optionally filters to
        dictations whose text matches a search phrase."""
        return store.list_dictations(limit=limit, query=query)

    @server.tool()
    def list_meetings(query: str | None = None, limit: int = 20) -> list[dict]:
        """List the user's recorded meetings, newest first (title, status, start/end time, but
        not the full transcript -- use get_meeting_transcript for that). `query` optionally
        filters to meetings matching a search phrase, in either the title or what was said."""
        return store.list_meetings(query=query)[:limit]

    @server.tool()
    def get_meeting_transcript(meeting_id: int) -> dict | None:
        """Get one meeting's full speaker-labeled transcript by id (from list_meetings), with
        each segment's speaker, track (mic = the user, system = everyone else), timestamps and
        text. Returns None if no meeting has that id."""
        return store.get_meeting(meeting_id)

    return server


def main() -> None:
    server = build_server(Store())
    server.run(transport="stdio")


if __name__ == "__main__":
    main()
