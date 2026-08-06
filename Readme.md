# Student ID Card Automation

Automatically extracts handwritten student information from scanned admission forms using **Google Lens AI Mode**, then fills a school ID card template with the extracted data and the student's photograph.

---

# Features

- Extract handwritten information
- Works with Hindi and English
- Converts Hindi text into English
- Automatically crops student photograph
- Detects face orientation
- Rotates photo correctly
- Enhances photograph quality
- Pastes photograph into template
- Auto-fits long names
- Auto-fits address
- Handles network interruptions
- Automatic retry system
- CAPTCHA handling
- Progress bar
- Logging
- Resume after internet reconnects

---

# Folder Structure

```
project/
│
├── image/
│      student1.jpg
│      student2.jpg
│      ...
│
├── output/
│
├── completed/
│
├── retry/
│
├── temp/
│
├── template.png
├── main.py
├── requirements.txt
└── README.md
```

---

# Requirements

## Operating System

Linux is recommended.

Tested on:

- Kali Linux
- Ubuntu
- Debian

---

## Python

Python 3.11+

Check version

```bash
python --version
```

---

# Install System Packages

Ubuntu / Debian / Kali

```bash
sudo apt update

sudo apt install \
python3-venv \
python3-pip \
fonts-dejavu-core \
libgl1 \
libglib2.0-0 \
libsm6 \
libxext6 \
libxrender-dev
```

---

# Create Virtual Environment

```bash
python -m venv venv
```

Activate

```bash
source venv/bin/activate
```

---

# Install Python Packages

```bash
pip install -r requirements.txt
```

---

# Install Playwright Browser

```bash
playwright install firefox
```

If dependencies are missing

```bash
playwright install-deps
```

---

# Firefox Profile

The script uses a persistent Firefox profile.

Current path:

```
/home/USERNAME/.mozilla/firefox/PROFILE.default-esr
```

Find yours

```bash
ls ~/.mozilla/firefox
```

Then edit

```python
user_data_dir="YOUR_FIREFOX_PROFILE"
```

inside

```python
launch_persistent_context(...)
```

---

# Google Login

Open Firefox once.

Login to your Google account.

Close Firefox.

Now run the script.

---

# Template

Place your ID card template here

```
template.png
```

---

# Input Images

Put all scanned forms inside

```
image/
```

Supported formats

- jpg
- jpeg
- png

---

# Output

Generated cards

```
output/
```

Successfully processed images

```
completed/
```

Failed images

```
retry/
```

Temporary screenshots

```
temp/
```

---

# Running

```bash
python main.py
```

---

# Processing Flow

```
Open Google Images

↓

Click Google Lens

↓

Upload Image

↓

Open AI Mode

↓

Ask Prompt

↓

Receive JSON

↓

Extract Student Data

↓

Crop Student Photo

↓

Enhance Photo

↓

Fill Template

↓

Save Output

↓

Move Original Image
```

---

# OCR Prompt

The script asks Google AI to return only JSON.

Fields extracted

- school_name
- student_name
- father_name
- mother_name
- class
- section
- roll_number
- mobile_number
- dob
- address
- student_photo_bbox

---

# Automatic Photo Processing

The script

- detects student face
- checks orientation
- rotates if necessary
- crops correctly
- enhances image
- rounds corners
- pastes into template

---

# Network Recovery

If internet disconnects

The script

- pauses
- waits for internet
- automatically resumes

No manual action required.

---

# CAPTCHA

If Google asks for CAPTCHA

The script waits.

Solve it manually.

Processing resumes automatically.

---

# Retry System

If

- JSON is invalid
- page fails
- upload fails

the image is copied to

```
retry/
```

After all images finish

the browser restarts

and retries every failed image.

---

# Logging

Detailed logs

```
temp/run_log.txt
```

Contains

- exceptions
- extracted JSON
- stack traces

---

# Temporary Screenshots

During processing

```
03_after_upload_full.png

03b_after_ai_mode.png

04_response_full.png
```

These are automatically deleted after successful processing.

---

# Customization

Template

```python
TEMPLATE_PATH
```

Photo Location

```python
PHOTO_BOX
```

Field Positions

```python
FIELD_POSITIONS
```

Fonts

```python
FONT_BOLD

FONT_REGULAR
```

Prompt

```python
PROMPT
```

---

# Notes

Google may occasionally

- ask for CAPTCHA
- change webpage layout
- change AI Mode location

If that happens, update the Playwright selectors in the script.

---

# Performance

Typical processing time

- 20–30 seconds per image

Depends on

- internet speed
- Google response time
- CAPTCHA frequency

---

# License

Use only on documents that you own or for which you have explicit permission to process.
