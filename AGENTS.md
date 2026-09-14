# Agent instructions — hikaxpro_hacs

Home Assistant custom integration for Hikvision AX Pro. Library dependency: [`petrleocompel/hikaxpro`](https://github.com/petrleocompel/hikaxpro).

When starting work here, also read `hikaxpro` `AGENTS.md` if that repo is available locally (sibling path `../hikaxpro/AGENTS.md`).

## Commit messages

- `feat: …`, `fix: …`, `docs: …`, `chore: …` — same conventional style as the library.

## Changelog

Update root `CHANGELOG.md` for every released integration version:

```markdown
## vX.Y.Z
- **fix**: …
- **feat**: …
```

## Version + library pin

In `custom_components/hikvision_axpro/manifest.json`:

- `"version"` — integration semver (bump when shipping)
- `"requirements"` — pin `hikaxpro==X.Y.Z` when adopting a new library release

Keep changelog header, manifest version, and pin change in sync in the same release commit set.

## Checklist

1. [ ] Commit style `feat:` / `fix:` / …
2. [ ] `CHANGELOG.md` updated
3. [ ] `manifest.json` version bumped
4. [ ] `hikaxpro` pin updated when the library fix/feature is required
5. [ ] Library published to PyPI (or user deferred) before relying on a new pin
