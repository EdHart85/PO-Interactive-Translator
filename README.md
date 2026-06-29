# PO Interactive Translator

[![Python](https://img.shields.io/badge/python-3.8%2B-blue.svg)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)

An interactive, terminal-based **CAT** (Computer-Assisted Translation) tool for
[Gettext](https://www.gnu.org/software/gettext/) **`.po`** files, powered by
Google Translate (via [`deep-translator`](https://pypi.org/project/deep-translator/)).

It was originally created to help translate the
[**Siril**](https://siril.org/) astrophotography software from English to
Portuguese, so the **defaults are tuned for Siril**. Every Siril-specific
behaviour is configurable, making the tool usable for **any project and any
language pair**.

## Features

- 🛡️ **Command protection** – keeps software command definitions untranslated
  (configurable PO occurrence marker; defaults to Siril's `Commands.rst:-1`).
- 🔒 **Inline protection** – preserves `` ``code`` `` and `**bold**` technical tokens.
- 🏷️ **Brand casing** – restores correct casing for protected names (regex-safe,
  handles `Starnet++`, `N.I.N.A`, etc.).
- 🌍 **Any language** – choose source/target via CLI (`auto` source supported).
- 🔢 **Plural & fuzzy aware** – handles `msgid_plural` and optional fuzzy re-translation.
- 💾 **Resumable** – autosaves every 10 entries and on Ctrl-C / quit.
- 🤖 **Batch mode** – `--auto` translates everything non-interactively.
- 🔁 **Resilient** – automatic retry with back-off and chunking for long strings.

## Installation

```bash
git clone https://github.com/your-handle/po-interactive-translator.git
cd po-interactive-translator
pip install -r requirements.txt
```

## Usage

```bash
# Interactive, Siril defaults (English -> Portuguese)
python po_interactive_translator.py siril.po

# Show full help
python po_interactive_translator.py --help
```

### Common options

| Option | Description | Default |
|--------|-------------|---------|
| `input` | Path to the `.po` (or `.po.txt`) file | prompt |
| `-o, --output` | Output file path | `<input>_translated.po` |
| `-l, --target-lang` | Target language code | `pt` |
| `-s, --source-lang` | Source language code (or `auto`) | `en` |
| `--brands-file` | File with protected names | `protected_brands.txt` |
| `--commands-marker` | PO marker for command entries (`""` disables) | `Commands.rst:-1` |
| `--auto` | Non-interactive batch translation | off |
| `--include-fuzzy` | Also re-translate fuzzy entries | off |
| `--no-color` | Disable ANSI colours | off |

### Examples

```bash
# Translate a generic app to Spanish, disable command protection
python po_interactive_translator.py app.po -l es --commands-marker ""

# Fully automatic, French, custom brand list
python po_interactive_translator.py app.po -l fr --auto --brands-file my_brands.txt
```

### Interactive keys

| Key | Action |
|-----|--------|
| `Enter` | Accept the suggested translation |
| `n` / `no` | Reject (leave empty for manual review) |
| `s` / `skip` | Skip without changing the entry |
| `q` / `quit` | Save progress and exit |
| *any text* | Save the typed text as a custom translation |

## License

[MIT](LICENSE)
