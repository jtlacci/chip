"""web: CHIP in the browser. Feed, feed instructions, chat and the agent panel.

The app is plain JavaScript in app/src (no packages). It talks only to the api
brick, through app/src/api.js. The server serves app/dist and answers /api/*.

The evaluation finds things on the page by these attributes. They are the
contract between the app and its evaluation, so they are human-only here:

| Attribute | On |
| --- | --- |
| body[data-ready] | set when the current page has rendered its data |
| [data-page="feed" / "chat" / "agent"] | the root of the current page |
| [data-nav="feed" / "chat" / "agent"] | the navigation links |
| [data-avatar] | CHIP's animated character |
| [data-post], [data-post-title] | one feed post, and its title |
| [data-instructions-open] | the button that opens the feed instructions |
| [data-instructions] dialog, [data-instructions-text], [data-instructions-save], [data-instructions-cancel] | the feed instructions dialog |
| [data-message] | one chat message |
| [data-composer], [data-send], input[data-attach], [data-attach-button], [data-attachment] | the message box, the send button, the file input, the button that opens the file picker, an attached file |
| [data-tab="activity" / "approvals"], [data-activity], [data-approval] | the agent panel tabs and their items |
| [data-signin] | the sign-in button, shown when there is no session |
"""


CONTRACT_VERSION = 1
KIND = "web"
LANE = "strict"
# With a fixed clock and fixed api answers, the same page renders the same way.
DETERMINISTIC = True
SIBLING_DEPENDENCIES: dict[str, str] = {}
OWNED_STATE: tuple[str, ...] = ()
