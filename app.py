import streamlit as st
import xml.etree.ElementTree as ET
import pdfplumber
import asyncio
import io
import re
import requests

st.set_page_config(page_title="EY Script Table Read", page_icon="🧛", layout="centered")

st.title("🧛 EY Script Table Read")
st.write("Upload a script (.fdx) to synthesize a multi-voice audio table read using ElevenLabs.")

# --- API KEY MANAGEMENT ---
api_key = st.secrets.get("ELEVENLABS_API_KEY", "")

# --- ELEVENLABS DEFAULT VOICE IDs ---
# These are popular pre-made stock voices available on free & paid ElevenLabs accounts
DEFAULT_VOICES = {
    "NARRATOR": "iP95p4xoKVk53GoZ742B",  # Chris
    "CHARLES": "yhf80q1381zd2JJQ4tM7",   # Dominic
    "LIZ": "r1KmysJdVYZjJCm4mL3b",       # Jessica
    "MAX": "3svOJAOhuPHXwQC2H5eq",       # Brady J
    "EMMA": "m3p6KEeXfVR68KjgMGgi",      # Veronica
    "JESSE": "rHWSYoq8UlV0YIBKMryp",     # Jerry B
    "PHYLLIS": "VdlAJiY20k9brfuVL9hQ",   # Diane
    "MORT": "u5CLDuTTFBRqALkkuvtX",      # Felix
    "HENRY": "3XOBzXhnDY98yeWQ3GdM",      # Brayden
    "BENJI": "gyIv9PAQRvJjSZlk68oE"     #Darius
}

AVAILABLE_VOICES = {
    "Rachel (Narrator / Calming Female)": "21m00Tcm4TlvDq8ikWAM",
    "Adam (Deep Male)": "pNInz6obpgDQGcFmaJgB",
    "Antoni (Well-Rounded Male)": "ErXwobaYiN019PkySvjV",
    "Bella (Expressive Female)": "EXAVITQu4vr4xnSDxMaL",
    "Arnold (Crisp / Authoritative)": "VR6AewLTigWG4xSOukaG",
    "Domi (Strong / Energetic)": "AZnzlk1XvdvUeBnXmlld",
    "Elli (Emotional / Young Female)": "MF3mGyEYCl7XYWbV9V6O",
    "Josh (Deep / Conversational)": "TxGEqnscrfW365w6gLM3"
}

# --- HELPER PARSERS ---

def clean_narrator_text(text):
    """Expands INT. and EXT. in sluglines to full words for clear audio synthesis."""
    text = re.sub(r'\bINT\.?\s*/\s*EXT\.?\b', 'INTERIOR/EXTERIOR', text, flags=re.IGNORECASE)
    text = re.sub(r'\bINT\.\b|\bINT\b', 'INTERIOR', text)
    text = re.sub(r'\bEXT\.\b|\bEXT\b', 'EXTERIOR', text)
    return text

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
                
                if clean.startswith(("INT.", "EXT.", "INT/EXT")) or (clean.isupper() and len(clean.split()) > 4):
                    script_lines.append({"speaker": "NARRATOR", "text": clean})
                    current_speaker = None
                elif clean.isupper() and len(clean.split()) <= 4 and not clean.endswith(":"):
                    current_speaker = re.sub(r'\s*\([^)]*\)', '', clean)
                elif current_speaker:
                    script_lines.append({"speaker": current_speaker, "text": clean})
                else:
                    script_lines.append({"speaker": "NARRATOR", "text": clean})
                    
    return script_lines

# --- ELEVENLABS GENERATION LOGIC ---

def generate_elevenlabs_speech(text, voice_id, api_key):
    """Generates MP3 audio buffer using ElevenLabs REST API directly (fast & dependency-free)."""
    url = f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}"
    headers = {
        "Accept": "audio/mpeg",
        "Content-Type": "application/json",
        "xi-api-key": api_key
    }
    payload = {
        "text": text,
        "model_id": "eleven_turbo_v2_5",  # Fastest & lowest-latency ElevenLabs model
        "voice_settings": {
            "stability": 0.5,
            "similarity_boost": 0.75
        }
    }
    
    response = requests.post(url, json=payload, headers=headers)
    if response.status_code == 200:
        return response.content
    else:
        raise Exception(f"HTTP {response.status_code}: {response.text}")

async def compile_table_read(script_lines, voice_assignments, api_key, progress_bar):
    """Generates audio for each line and stitches raw MP3 byte streams directly."""
    combined_audio = bytearray()
    total_lines = len(script_lines)
    
    for idx, line in enumerate(script_lines):
        speaker = line["speaker"]
        text = line["text"]
        
        if speaker == "NARRATOR":
            text = clean_narrator_text(text)
            
        voice_id = voice_assignments.get(speaker, AVAILABLE_VOICES["Rachel (Narrator / Calming Female)"])
        
        try:
            # ElevenLabs generation call
            audio_bytes = generate_elevenlabs_speech(text, voice_id, api_key)
            combined_audio.extend(audio_bytes)
        except Exception as e:
            st.warning(f"Skipped line {idx+1} ({speaker}) due to synthesis error: {e}")
            
        progress_bar.progress((idx + 1) / total_lines)
        
    return bytes(combined_audio)

# --- APP INTERFACE ---

uploaded_file = st.file_uploader("Upload Screenplay (.fdx)", type=["fdx"])

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
        
        st.subheader("🎙️ Character Voice Assignments (ElevenLabs Voice IDs)")
        
        assigned_voices = {}

        # Narrator Setup
        assigned_voices["NARRATOR"] = DEFAULT_VOICES.get("NARRATOR")
        st.write(f"🔒 **NARRATOR** → Locked to default model (`{DEFAULT_VOICES['NARRATOR'][:8]}`)")

        # Character Setup
        for idx, character in enumerate(detected_characters):
            char_upper = character.upper().strip()
            
            if char_upper in DEFAULT_VOICES:
                assigned_voices[character] = DEFAULT_VOICES[char_upper]
                st.write(f"🔒 **{character}** → Locked to default model `{DEFAULT_VOICES[char_upper][:8]}...`")
            else:
                selected_label = st.selectbox(
                    f"Select voice for {character}:",
                    options=list(AVAILABLE_VOICES.keys()),
                    key=f"voice_{character}"
                )
                custom_id = st.text_input(
                    f"Or paste custom Voice ID for {character} (optional):", 
                    value="", 
                    key=f"custom_{character}"
                )
                assigned_voices[character] = custom_id.strip() if custom_id.strip() else AVAILABLE_VOICES[selected_label]

        # Trigger Synthesis
        if st.button("▶️ Generate Table Read"):
            if not api_key:
                st.error("Please provide an ElevenLabs API key in the sidebar before generating.")
            else:
                progress_bar = st.progress(0.0)
                status_text = st.empty()
                status_text.text("Synthesizing character voices with ElevenLabs...")
                
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                final_audio_bytes = loop.run_until_complete(
                    compile_table_read(script_lines, assigned_voices, api_key, progress_bar)
                )
                
                status_text.text("Table Read Ready!")
                st.audio(final_audio_bytes, format="audio/mp3")
                
                st.download_button(
                    label="⬇️ Download Table Read (MP3)",
                    data=final_audio_bytes,
                    file_name=f"{uploaded_file.name}_table_read.mp3",
                    mime="audio/mp3"
                )
