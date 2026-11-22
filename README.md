# MCQ-OMR

Multiple-Choice-Questions-Optical-Mark-Recognition

I wrote this to run multiple-choice tests in my classes without marking them by hand, and without students being able to copy from each other.

Every student gets a different version of the same exam (questions in a different order, choices shuffled) and a one-page answer sheet with bubbles, made for that exam set only. After the test, the sheets are photographed with a phone. The program reads the bubbles, works out the version from the QR code on the sheet and marks it.

It works for any school, college or university. It runs on your own computer and you use it in your web browser (it talks with your own computer, nothing goes to the internet).

The interface is available in English, Português (Brasil), Español, Français, Deutsch, Italiano and 中文（简体）. Choose the language when installing, in the app window or in Settings ("Automatic" follows the browser).

## What it does

- **Exams**: type or paste the questions (one block per question, `*` before the correct choice, and optionally a `TOPIC:` line), then generate as many shuffled versions as you need.
- **Printing**: one PDF with every student's answer sheet followed by their paper, plus double-sided printing and answer keys.
- **Reading the sheets**:
  - with the phone app over your Wi-Fi;
  - with a webcam;
  - by uploading photos or a zip of photos.

  Sheets that need a second look (a double mark, a faint mark, an unreadable student number) are flagged for review, where you can correct them.
- **Results**:
  - the results table, sortable and filterable by course;
  - an Excel export;
  - statistics for the class and for each question (how hard it was, how well it separates strong and weak students, which wrong answers attracted whom), and by topic when the questions have topics;
  - a page and a PDF report for each student;
  - a "reveal" page to show each student their mark as their sheet is scanned.
- **Statistics for students**: the same exam figures with no names, numbers or marks, to show the class how the exam went. The correct answers stay hidden until you reveal them.
- **Progress**: compare MCQs of the same module: who missed one, who improved or declined, and the class change.
- **Backups**: one .zip file with everything, from Settings or from the app window. Restore it on the same or another computer.
- **Students and courses (optional)**: paste your class list, put students into courses and copy the email addresses of any list of students. An exam set can be given a class: its results and statistics then open on the students of that class, and it lists the students of the class not scanned yet.

## Install (no technical knowledge needed)

Download the installer for your computer from the **[Releases page](https://github.com/guilhermedanielgomes/mcq-omr/releases)**.

| Computer | File | How to install |
|---|---|---|
| Windows 10 / 11 | `MCQ-OMR-…-windows-setup.exe` | Double-click it and follow the steps. |
| Mac with Apple silicon (M1 or newer) | `MCQ-OMR-…-macos-arm64.dmg` | Open it and drag **MCQ-OMR** onto **Applications**. |
| Older Intel Mac | `MCQ-OMR-…-macos-x86_64.dmg` | The same as above. |
| Ubuntu, Debian, Mint… | `mcq-omr_…_amd64.deb` | Double-click it to open it in the software centre, or run `sudo apt install ./mcq-omr_…_amd64.deb`. |
| Other Linux | `MCQ-OMR-…-linux-x86_64.tar.gz` | Extract it and run `MCQ-OMR` inside the folder. |

**The first time you open it**, your computer may warn you if that installer is not signed with a developer certificate:

- **Windows** shows "Windows protected your PC". Click **More info**, then **Run anyway**. If Windows asks whether to allow MCQ-OMR on networks, allow **private networks**: the phone needs this to send photos.
- **macOS** says it "cannot be opened" or "cannot verify" the app. Open **System Settings → Privacy & Security**, scroll down and click **Open Anyway** next to MCQ-OMR. If macOS asks to allow incoming connections or access to the local network, click **Allow**: the phone needs this.

### Using it

Open **MCQ-OMR** like any other app (Start menu, Launchpad / Applications, or your applications menu).

- The app opens in your web browser, at `http://localhost:8000`.
- A small MCQ-OMR window shows two addresses: one for this computer, and one to type into the phone app. It also shows the password the phone needs (see Privacy below).
- The phone must be on the same Wi-Fi network as the computer.
- **Keep the small window open while you work. Closing it stops MCQ-OMR.**

If you open the app while it is already running, it just opens the browser again.

### Where your data is kept

Your exams, scanned sheets, students and settings are kept in a folder of their own. The **Open data folder** button in the MCQ-OMR window takes you there.

| Computer | Folder |
|---|---|
| Windows | `%APPDATA%\MCQ-OMR` |
| macOS | `~/Library/Application Support/MCQ-OMR` |
| Linux | `~/.local/share/mcq-omr` |

To back up, use **Back up…** in the MCQ-OMR window or **Settings → Backup → Download a backup** in the browser. Either one makes a single .zip file with all exams, scanned sheets and their photos, students and settings. To move to another computer, install MCQ-OMR there and use **Restore a backup…** (or **Settings → Backup → Restore**). Before restoring, MCQ-OMR saves the current data as a backup in the `backups` folder of the data folder, so a restore of the wrong file can be undone.

A backup holds your students' names and results: keep it somewhere only you can open.

### Updating and uninstalling

To update, install the new version over the old one. Your data stays where it is.

To uninstall:

- **Windows**: Settings → Apps → MCQ-OMR → Uninstall.
- **macOS**: drag MCQ-OMR from Applications to the Bin.
- **Linux**: remove it in the software centre, or run `sudo apt remove mcq-omr`.

Uninstalling **does not delete your data**. Delete the data folder above yourself if you want it gone.

## Privacy

- MCQ-OMR does not contact any server on the internet: no accounts, no analytics, no fonts or scripts loaded from elsewhere. Exams, photos, students and results stay in the data folder on your computer.
- The phone app sends photos directly to your computer over your Wi-Fi network.
- **A password for other devices.** MCQ-OMR listens on your network so the phone app can reach it. To keep others on the same Wi-Fi out, every phone, tablet or computer other than the one running MCQ-OMR needs a password. The computer running MCQ-OMR never needs it.
  - A new installation has this switched on, with a password made for it. An installation that already had exams when it was updated keeps working as before, without a password, until you switch it on in **Settings → Network**. When it is off, anyone on the same network who knows the address can open every page, including student names and results.
  - The password and a pairing QR code are in **Settings → Network** and in the small MCQ-OMR window. **New password** makes another one; the old one stops working on every device.
  - **Pairing the phone:** type the password into the phone app's password field (next to the server address), or scan the QR code with a phone app that supports pairing. The QR code holds `mcqomr://pair?u=<server address>&t=<password>`.
  - Another computer, such as a projector laptop for the reveal page, asks for the password once and then stays signed in.
  - Without a window (`run.py`, Docker), the password is printed when the server starts. In Docker every browser, the host's too, counts as another device.
- Pages refuse form posts sent from other web sites, so a page you visit cannot change your data in MCQ-OMR behind your back.
- On a network you do not control (a school or campus Wi-Fi), keep the password switched on, or use a private network.

## Run from the source code (for developers)

You need Python 3.11 or newer (tested with 3.13).

```sh
git clone https://github.com/guilhermedanielgomes/mcq-omr.git
cd mcq-omr
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r server/requirements.txt
cd server
python run.py                      # or: python desktop.py   (the same small window as the installed app)
```

- `run.py` keeps the data in `server/data/`. `desktop.py` uses the per-user folder above.
- Set `MCQ_DATA=/some/folder` to choose another folder, and `MCQ_PORT` (desktop.py) or `python run.py 8080` to use another port.
- **Optional access token**: set `MCQ_TOKEN=some-secret` before starting. Every device then needs it, this computer too: the browser asks for it once, and the phone app sends it as its password. While it is set, it is used instead of the password in Settings → Network.
- **Docker** (for example on a home server): see `server/Dockerfile`.
- **Tests**: `python tests/synthetic_test.py` (needs `pip install pymupdf`). It makes fake phone photos of filled answer sheets (rotated, blurred, shadowed, upside down, ticks instead of fills) and checks that they are read correctly.

### Building the installers

The installers are built by GitHub Actions (`.github/workflows/installers.yml`), because each one has to be built on its own system.

- Run the workflow by hand from the **Actions** tab to get the files as downloads.
- Push a tag such as `v1.0.0` to also attach them to a Release.
- The build uses the exact package versions in `packaging/requirements-build.txt`, so every installer reads sheets the same way as the tested one.

**Signing for macOS.** Without signing, macOS warns on first open. With an Apple Developer account, add these repository secrets (Settings → Secrets and variables → Actions) and the workflow signs and notarizes the disk image:

| Secret | What |
|---|---|
| `MACOS_CERT_P12` | your "Developer ID Application" certificate exported from Keychain Access as .p12, then base64-encoded (`base64 -i cert.p12 \| pbcopy`) |
| `MACOS_CERT_PASSWORD` | the password chosen when exporting the .p12 |
| `MACOS_SIGN_IDENTITY` | the certificate name, e.g. `Developer ID Application: Your Name (TEAMID)` |
| `APPLE_ID` | your Apple ID email |
| `APPLE_TEAM_ID` | your 10-character Team ID |
| `APPLE_APP_PASSWORD` | an app-specific password from appleid.apple.com |

**Translations.** Texts are marked in the code (`_()` in templates, `t()` in JavaScript, `tr()` in Python). `python tools/i18n_extract.py` (from `server/`) adds new texts to `omr/locales/*.json`; a text without a translation shows in English. To add a language, add it to `LANGUAGES` in `omr/i18n.py`, run the script and translate the new file.

To build the Mac app yourself, use a Python from python.org (it includes Tk, which the small window needs):

```sh
pip install -r packaging/requirements-build.txt
pyinstaller packaging/mcq-omr.spec --noconfirm
sh packaging/macos/build_dmg.sh 1.0.0 dist
```

## Licence

MIT, see [LICENSE](LICENSE). Developed by GGSDev ([guilherme.ie](https://guilherme.ie)).
