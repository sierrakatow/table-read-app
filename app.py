import streamlit as st
import xml.etree.ElementTree as ET
import pdfplumber
import asyncio
import edge_tts
import io
import os
import re
from pydub import AudioSegment

st.set_page_config(page_title="Table Read AI", page_icon="🎭", layout="centered")

st.title("🎭 Multi-Voice Screenplay Table Read")
st.write("Upload a 37-page TV script (.fdx or .pdf) to automatically assign voices and perform a full audio table read.")

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

def parse_fdx(file_bytes):
    """Parses Final Draft XML structure into sequential lines."""
    tree = ET.ElementTree(ET.fromstring(file_bytes))
    root = tree.getroot()
    
    script_lines = []
    current_speaker = None
    
    for element in root.findall(".//Element"):
        element_type = element.get("Type")
        text = "".join(element.itertext()).strip()
        
        if not text:
            continue
            
        if element_type == "Character":
            # Clean character extensions like (O.S.) or (V.O.)
            current_speaker = re.sub(r'\s*\([^)]*\)', '', text).upper()
        elif element_type == "Dialogue" and current_speaker:
            script_lines.append({"speaker": current_speaker, "text": text})
            current_speaker = None
        elif element_type in ["Action", "Scene Heading"]:
            script_lines.append({"speaker": "NARRATOR", "text": text})
            current_speaker = None
            
    return script_lines

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
                
                # Detect Scene Headings / Action vs Dialogue
                if clean.startswith(("INT.", "EXT.", "INT/EXT")) or clean.isupper() and len(clean.split()) > 4:
                    script_lines.append({"speaker": "NARRATOR", "text": clean})
                    current_speaker = None
                # All uppercase short string = Character name
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
    """Generates audio for each line and stitches them into a single track."""
    combined_audio = AudioSegment.empty()
    total_lines = len(script_lines)
    
    for idx, line in enumerate(script_lines):
        speaker = line["speaker"]
        text = line["text"]
        voice = voice_assignments.get(speaker, DEFAULT_NARRATOR_VOICE)
        
        try:
            audio_bytes = await generate_speech(text, voice)
            segment = AudioSegment.from_file(io.BytesIO(audio_bytes), format="mp3")
            combined_audio += segment + AudioSegment.silent(duration=300) # Add 300ms pause between lines
        except Exception as e:
            st.warning(f"Skipped line {idx+1} due to synthesis error: {e}")
            
        progress_bar.progress((idx + 1) / total_lines)
        
    out_buffer = io.BytesIO()
    combined_audio.export(out_buffer, format="mp3")
    return out_buffer.getvalue()

# --- APP INTERFACE ---

uploaded_file = st.file_uploader("Upload Screenplay (.fdx or .pdf)", type=["fdx", "pdf"])

if uploaded_file:
    file_bytes = uploaded_file.read()
    file_type = uploaded_file.name.split(".")[-1].lower()
    
    with st.spinner("Parsing screenplay..."):
        if file_type == "fdx":
            script_lines = parse_fdx(file_bytes)
        else:
            script_lines = parse_pdf(file_bytes)

    if not script_lines:
        st.error("Could not extract any lines from this file. Please ensure it is a formatted script.")
    else:
        st.success(f"Parsed {len(script_lines)} lines!")
        
        # Extract unique characters
        characters = sorted(list(set(l["speaker"] for l in script_lines if l["speaker"] != "NARRATOR")))
        
        st.subheader("🎙️ Voice Assignments")
        st.info("Assign a unique voice for each character in your episode.")
        
        voice_assignments = {}
        
        # Assign Narrator Voice
        voice_assignments["NARRATOR"] = st.selectbox(
            "Narrator (Action & Sluglines)", 
            options=list(AVAILABLE_VOICES.values()),
            format_func=lambda x: [k for k, v in AVAILABLE_VOICES.items() if v == x][0],
            index=1
        )
        
        # Assign Character Voices
        voice_options = list(AVAILABLE_VOICES.values())
        for idx, char in enumerate(characters):
            default_voice_idx = (idx + 2) % len(voice_options)
            voice_assignments[char] = st.selectbox(
                f"Character: {char}",
                options=voice_options,
                format_func=lambda x: [k for k, v in AVAILABLE_VOICES.items() if v == x][0],
                index=default_voice_idx
            )

        # Generate Button
        if st.button("▶️ Generate Table Read"):
            progress_bar = st.progress(0.0)
            status_text = st.empty()
            status_text.text("Synthesizing character voices...")
            
            # Run Async TTS in Event Loop
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            final_audio_bytes = loop.run_until_complete(
                compile_table_read(script_lines, voice_assignments, progress_bar)
            )
            
            status_text.text("Table Read Ready!")
            st.audio(final_audio_bytes, format="audio/mp3")
            
            st.download_button(
                label="⬇️ Download Table Read (MP3)",
                data=final_audio_bytes,
                file_name=f"{uploaded_file.name}_table_read.mp3",
                mime="audio/mp3"
            )
