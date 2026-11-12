# rapport

**Your agent writes the report. You give feedback. Repeat.**

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/review-dark.png">
  <img alt="A rapport report in the browser: a selected passage with the comment window open, its quick actions, and two earlier comments highlighted" src="docs/review-light.png">
</picture>

Getting a report from an agent is quick. Fixing it in chat is not: "the third paragraph in section two is too long" gets old fast.

With rapport, you review the report like a document instead. Select a sentence, type a comment or flag it with one click, and tell your agent to apply the feedback. It knows exactly where each comment sits, fixes it, and the page reloads in front of you.

The finished report is a single HTML file. It opens anywhere, works offline, and is easy to send around.

## Get started

You need macOS or Linux and [uv](https://docs.astral.sh/uv/) (`brew install uv`).

```sh
git clone https://github.com/andrewendlinger/rapport ~/code/rapport
~/code/rapport/install.sh --bin
```

That's it. rapport works with Claude Code, Codex, pi and any other agent that supports Agent Skills. Open a new session and ask:

> Write up the cache regression as a rapport.

Your agent writes it and gives you a link. Leave your comments, go back to the agent and say *"apply the feedback"*. The primer explains [how reviewing works](examples/primer/report.md#3--review-it).

To update, run `git pull` in `~/code/rapport`.

**Want a look first?** The [primer](examples/primer/report.md) is a five-minute tour of reviewing, writing and customising, and it's a rapport report itself. Serve a copy to try it:

```sh
cp -R ~/code/rapport/examples/primer /tmp/primer
rapport serve /tmp/primer
```

**What gets installed:** links in `~/.claude/skills` (Claude Code) and `~/.agents/skills` (pi and other agents), plus the `rapport` command in `~/.local/bin`. They point at your clone, so `git pull` updates everything. Another agent? `./install.sh --target <its skills folder>`. All options: `./install.sh --help`.

**Uninstall:** `~/code/rapport/install.sh --uninstall`, then delete the folder. Your settings live in `~/.config/rapport` if you want those gone too.

## Make it yours

In rapport, everything is a config line or a module. You rarely write either yourself: tell your agent what you want, and it sets it up.

- *"Write for the on-call team: short, plain, times in UTC."* (config)
- *"Make my reports look like our brand, with our colours, fonts and logo."* (config)
- *"Give reviewers a 'Needs source' button."* (config)
- *"Learn from how I correct you, and suggest rules for next time."* (config)
- *"Add a timeline block for project milestones."* (module)
- *"Put a sign-off button at the end, so reviewers can approve the report."* (module)
- *"Add a quick poll for the team."* (module)

Config is a few lines of TOML, for all your reports, one project or a single report. A module is a small folder with a template, and it can even remember what readers clicked while the report is served. The primer shows both: [making it yours](examples/primer/report.md#making-it-yours) and [adding a module](examples/primer/report.md#adding-a-module).

### Confluence

If your reports end up in Confluence, add this line to `~/.config/rapport/config.toml`:

```toml
preset = "confluence"
```

Reports then look like the Confluence page they'll become. Your agent can publish them, and bring edits that people make in Confluence back into the report. [How it works](references/confluence.md)

## Learn more

- [Primer](examples/primer/report.md): the five-minute tour, start here
- [Writing reports](references/authoring.md): what goes into `report.md`
- [Configuration](references/config.md): themes, rules and where settings live
- [Confluence](references/confluence.md): publishing and syncing edits
- [Changelog](CHANGELOG.md)

## Contributing

Issues and pull requests are welcome. [AGENTS.md](AGENTS.md) explains how the code fits together and how to run the tests.

## License

[MIT](LICENSE)