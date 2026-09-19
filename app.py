import streamlit as st
import xml.etree.ElementTree as ET
import pdfplumber
import asyncio
import io
import re
from fishaudio import AsyncFishAudio

st.set_page_config(page_title="Table Read AI", page_icon="🎭", layout="centered")

st.title("🎭 Multi-Voice Screenplay Table Read")
st.write("Upload a script (.fdx or .pdf) to synthesize a multi-voice audio table read using Fish Audio.")

# --- API KEY MANAGEMENT ---
st.sidebar.title("⚙️ Settings")
api_key = st.sidebar.text_input(
    "Fish Audio API Key", 
    type="password", 
    value=st.secrets.get("FISH_API_KEY", "sk-fish-Y20xJ0DYokx1ID1UhOgpjsAcIFkyXHIFdfsG7OUTylg")
)

if not api_key:
    st.sidebar.warning("⚠️ Enter your Fish Audio API key to enable speech generation.")

# --- DEFAULT FISH AUDIO VOICE MODEL IDs ---
DEFAULT_VOICES = {
    "NARRATOR": "0327fdb5da9e4fd782899a8058c8ae2b",
    "CHARLES": "0f41db07117e46b39bc270043d0e48bf",
    "LIZ": "933563129e564b19a115bedd57b7406a",
    "MAX": "b5f4515fd395410b9ed3aef6fa51d9a0",
    "EMMA": "fb43143e46f44cc6ad7d06230215bab6",
    "JESSE": "04fc00f697174a778cc93b9ebc374b88",
    "PHYLLIS": "06366bd48deb46d98e0df6160f798cb3",
    "MORT": "04223913e8bc49aebdb01469d7a58713",
    "HENRY": "50abc5fb921a4872908b2bf2ac735ab1"
}

AVAILABLE_VOICES = {
    "Narrator / Conversational Male": "9a9cf47702da476aa4629e2506d4a857",
    "Dramatic Male": "80242207b5ec4310a08e67303e2e0136",
    "Expressive Female": "651a24d5ff1a41869e99e2f9d5019053",
    "Deep British Male": "3775f04a62144342894191a343469904"
}

# --- HELPER PARSERS ---

def clean_narrator_text(text):
    """Expands INT. and EXT. in sluglines to full words for clear audio synthesis."""
    # Handle combined INT/EXT variations first
    text = re.sub(r'\bINT\.?\s*/\s*EXT\.?\b', 'INTERIOR/EXTERIOR', text, flags=re.IGNORECASE)
    # Handle standalone INT. and EXT.
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

# --- FISH AUDIO GENERATION LOGIC ---

async def generate_speech(client, text, reference_id):
    """Generates MP3 audio buffer using Fish Audio Async SDK."""
    # Option 1: Direct await (returns complete audio bytes)
    audio_bytes = await client.tts.convert(
        text=text,
        reference_id=reference_id,
        format="mp3",
        latency="balanced"
    )
    return audio_bytes

async def compile_table_read(script_lines, voice_assignments, api_key, progress_bar):
    """Generates audio for each line and stitches raw MP3 byte streams directly."""
    combined_audio = bytearray()
    total_lines = len(script_lines)
    
    client = AsyncFishAudio(api_key=api_key)
    
    for idx, line in enumerate(script_lines):
        speaker = line["speaker"]
        text = line["text"]
        
        # Clean narrator sluglines before TTS conversion
        if speaker == "NARRATOR":
            text = clean_narrator_text(text)
            
        voice_id = voice_assignments.get(speaker, AVAILABLE_VOICES["Narrator / Conversational Male"])
        
        try:
            audio_bytes = await generate_speech(client, text, voice_id)
            combined_audio.extend(audio_bytes)
        except Exception as e:
            st.warning(f"Skipped line {idx+1} ({speaker}) due to synthesis error: {e}")
            
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
        
        st.subheader("🎙️ Character Voice Assignments (Fish Audio Model IDs)")
        
        assigned_voices = {}

        # Narrator Setup
        narrator_voice_default = DEFAULT_VOICES.get("NARRATOR", AVAILABLE_VOICES["Narrator / Conversational Male"])
        narrator_label = st.selectbox(
            "Narrator (Action & Sluglines)", 
            options=list(AVAILABLE_VOICES.keys()),
            index=0
        )
        custom_narrator_id = st.text_input("Or paste custom Voice ID for Narrator (optional):", value="", key="custom_narrator")
        assigned_voices["NARRATOR"] = custom_narrator_id.strip() if custom_narrator_id.strip() else AVAILABLE_VOICES[narrator_label]

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
                st.error("Please provide a Fish Audio API key in the sidebar before generating.")
            else:
                progress_bar = st.progress(0.0)
                status_text = st.empty()
                status_text.text("Synthesizing character voices with Fish Audio...")
                
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
