import streamlit as st
import xml.etree.ElementTree as ET
import pdfplumber
import asyncio
import edge_tts
import io
import os
import re

st.set_page_config(page_title="Table Read AI", page_icon="🎭", layout="centered")

st.title("🎭 Multi-Voice Screenplay Table Read")
st.write("Upload a script (.fdx or .pdf) to automatically assign voices and perform a full audio table read.")

# Hardcoded regular cast
DEFAULT_VOICES = {
    "NARRATOR": "en-US-GuyNeural",
    "CHARLES": "en-GB-RyanNeural",
    "LIZ": "en-US-JennyNeural",
    "MAX": "en-US-ChristopherNeural",
    "EMMA": "en-US-AriaNeural",
    "JESSE": "en-US-EricNeural",
    "PHYLLIS": "en-US-AnaNeural",
    "MORT": "en-US-RogerNeural",
    "HENRY": "en-US-SteffanNeural",
    "STEVE": "en-US-RogerNeural"
}

# Standard default Edge TTS voices
AVAILABLE_VOICES = {
    "Male - US (Guy)": "en-US-GuyNeural",
    "Male - US (Christopher)": "en-US-ChristopherNeural",
    "Male - UK (Ryan)": "en-GB-RyanNeural",
    "Male - AU (William)": "en-AU-WilliamNeural",
    "Female - US (Aria)": "en-US-AriaNeural",
    "Female - US (Jenny)": "en-US-JennyNeural",
    "Female - UK (Sonia)": "en-GB-SoniaNeural",
    "Female - AU (Natasha)": "en-AU-NatashaNeural",
}

DEFAULT_NARRATOR_VOICE = "en-US-ChristopherNeural"

# --- HELPER PARSERS ---

def repair_truncated_xml(raw_bytes):
    """Attempts to fix incomplete XML by appending missing closing tags."""
    content = raw_bytes.decode('utf-8', errors='ignore')
    content = re.sub(r'<[^>]*$', '', content)
    
    open_tags = re.findall(r'<([a-zA-Z0-9_]+)(?:\s+[^/>]*)?>', content)
    close_tags = re.findall(r'</([a-zA-Z0-9_]+)>', content)
    
    stack = []
    for tag in open_tags:
        stack.append(tag)
        
    for tag in close_tags:
        if tag in stack:
            stack.remove(tag)
            
    for tag in reversed(stack):
        content += f"</{tag}>"
        
    return content.encode('utf-8')


def parse_fdx(file_bytes):
    """Parses Final Draft XML structure into sequential lines."""
    script_lines = []
    is_partial = False

    if b'</FinalDraft>' not in file_bytes[-100:]:
        is_partial = True

    try:
        repaired_bytes = repair_truncated_xml(file_bytes)
        root = ET.fromstring(repaired_bytes)
        
        current_speaker = None
        elements = root.findall(".//Paragraph") or root.findall(".//Element")
        
        for element in elements:
            element_type = element.get("Type")
            text = "".join(element.itertext()).strip()
            
            if not text:
                continue
                
            if element_type == "Character":
                current_speaker = re.sub(r'\s*\([^)]*\)', '', text).upper()
            elif element_type == "Dialogue" and current_speaker:
                script_lines.append({"speaker": current_speaker, "text": text})
                current_speaker = None
            elif element_type in ["Action", "Scene Heading"]:
                script_lines.append({"speaker": "NARRATOR", "text": text})
                current_speaker = None

        return script_lines, is_partial

    except Exception:
        is_partial = True
        content_str = file_bytes.decode('utf-8', errors='ignore')
        
        pattern = re.compile(
            r'<(?:Paragraph|Element)[^>]*Type="(?P<type>[^"]+)"[^>]*>(.*?)(?=</(?:Paragraph|Element)>|<(?:Paragraph|Element)|$)', 
            re.DOTALL
        )
        text_pattern = re.compile(r'<Text[^>]*>(.*?)</Text>', re.DOTALL)

        current_speaker = None

        for match in pattern.finditer(content_str):
            element_type = match.group('type')
            body = match.group(2)
            
            texts = text_pattern.findall(body)
            if texts:
                text = "".join(texts).strip()
            else:
                text = re.sub(r'<[^>]+>', '', body).strip()

            if not text:
                continue

            if element_type == "Character":
                current_speaker = re.sub(r'\s*\([^)]*\)', '', text).upper()
            elif element_type == "Dialogue" and current_speaker:
                script_lines.append({"speaker": current_speaker, "text": text})
                current_speaker = None
            elif element_type in ["Action", "Scene Heading"]:
                script_lines.append({"speaker": "NARRATOR", "text": text})
                current_speaker = None

        return script_lines, is_partial


def parse_pdf(file_bytes):
    """Basic heuristic parser for screenplay PDFs based on margins."""
    script_lines = []
    current_speaker = None
    
    with pdfplumber.open(io.BytesIO(file_bytes)) as pdf:
        for page in pdf.pages:
            text = page.extract_text()
            if not text:
                continue
            
            lines = text.split("\n")
            for line in lines:
                clean = line.strip()
                if not clean:
                    continue
                
                if clean.startswith(("INT.", "EXT.", "INT/EXT")) or clean.isupper() and len(clean.split()) > 4:
                    script_lines.append({"speaker": "NARRATOR", "text": clean})
                    current_speaker = None
                elif clean.isupper() and len(clean.split()) <= 4 and not clean.endswith(":"):
                    current_speaker = re.sub(r'\s*\([^)]*\)', '', clean)
                elif current_speaker:
                    script_lines.append({"speaker": current_speaker, "text": clean})
                else:
                    script_lines.append({"speaker": "NARRATOR", "text": clean})
                    
    return script_lines

# --- TTS GENERATOR ---

async def generate_speech(text, voice):
    """Generates MP3 audio buffer for a single string of text."""
    communicate = edge_tts.Communicate(text, voice)
    audio_data = bytearray()
    async for chunk in communicate.stream():
        if chunk["type"] == "audio":
            audio_data.extend(chunk["data"])
    return bytes(audio_data)

async def compile_table_read(script_lines, voice_assignments, progress_bar):
    """Generates audio for each line and stitches raw MP3 byte streams directly."""
    combined_audio = bytearray()
    total_lines = len(script_lines)
    
    for idx, line in enumerate(script_lines):
        speaker = line["speaker"]
        text = line["text"]
        voice = voice_assignments.get(speaker, DEFAULT_NARRATOR_VOICE)
        
        try:
            audio_bytes = await generate_speech(text, voice)
            combined_audio.extend(audio_bytes)
        except Exception as e:
            st.warning(f"Skipped line {idx+1} due to synthesis error: {e}")
            
        progress_bar.progress((idx + 1) / total_lines)
        
    return bytes(combined_audio)

# --- APP INTERFACE ---

uploaded_file = st.file_uploader("Upload Screenplay (.fdx or .pdf)", type=["fdx", "pdf"])

if uploaded_file:
    file_bytes = uploaded_file.read()
    file_type = uploaded_file.name.split(".")[-1].lower()
    
    is_partial_recovery = False

    with st.spinner("Parsing screenplay..."):
        if file_type == "fdx":
            script_lines, is_partial_recovery = parse_fdx(file_bytes)
        else:
            script_lines = parse_pdf(file_bytes)

    if not script_lines:
        st.error("Could not extract any lines from this file. Please ensure it is a valid script.")
    else:
        if is_partial_recovery:
            st.warning("⚠️ The uploaded .fdx file appears truncated or incomplete. Recovered as many valid lines as possible.")
        else:
            st.success(f"Parsed {len(script_lines)} lines!")
        
        detected_characters = sorted(list(set(l["speaker"] for l in script_lines if l["speaker"] != "NARRATOR")))
        
        st.subheader("🎙️ Character Voice Assignments")
        
        assigned_voices = {}

        assigned_voices["NARRATOR"] = st.selectbox(
            "Narrator (Action & Sluglines)", 
            options=list(AVAILABLE_VOICES.values()),
            format_func=lambda x: [k for k, v in AVAILABLE_VOICES.items() if v == x][0],
            index=1
        )
        
        voice_options = list(AVAILABLE_VOICES.values())
        for idx, character in enumerate(detected_characters):
            char_upper = character.upper().strip()
            
            if char_upper in DEFAULT_VOICES:
                default_voice_code = DEFAULT_VOICES[char_upper]
                assigned_voices[character] = default_voice_code
                st.write(f"🔒 **{character}** → Automatically locked to `{default_voice_code}`")
            else:
                default_voice_idx = (idx + 2) % len(voice_options)
                assigned_voices[character] = st.selectbox(
                    f"Select voice for {character}:",
                    options=voice_options,
                    format_func=lambda x: [k for k, v in AVAILABLE_VOICES.items() if v == x][0],
                    index=default_voice_idx,
                    key=f"voice_{character}"
                )

        if st.button("▶️ Generate Table Read"):
            progress_bar = st.progress(0.0)
            status_text = st.empty()
            status_text.text("Synthesizing character voices...")
            
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            final_audio_bytes = loop.run_until_complete(
                compile_table_read(script_lines, assigned_voices, progress_bar)
            )
            
            status_text.text("Table Read Ready!")
            st.audio(final_audio_bytes, format="audio/mp3")
            
            st.download_button(
                label="⬇️ Download Table Read (MP3)",
                data=final_audio_bytes,
                file_name=f"{uploaded_file.name}_table_read.mp3",
                mime="audio/mp3"
            )
