import numpy as np
import subprocess
import os
from scipy.io import wavfile
from scipy.signal import butter, lfilter

INPUT_DIR = r'./data/Ipod_Music' 
    
OUTPUT_DIR = r'./data/Spatial_proccessed'

BIN_DIR = "bin"

FFMPEG_PATH = os.path.join(BIN_DIR, "ffmpeg.exe") 

SURROUND_DELAY_MS = 20
SURROUND_CUTOFF = 7000

def butter_lowpass(cutoff, fs, order=5):
    nyq = 0.5 * fs
    normal_cutoff = cutoff / nyq
    b, a = butter(order, normal_cutoff, btype='low', analog=False)
    return b, a

def apply_lowpass(data, cutoff, fs, order=5):
    b, a = butter_lowpass(cutoff, fs, order=order)
    return lfilter(b, a, data)

"""
Audio spatial method using binaural proccesing 
"""

def process_spatial_audio(input_path, output_path):
    print(f"\nProccesing: {os.path.basename(input_path)}")
    
    temp_in_wav = "temp_decode.wav"
    temp_out_wav = "temp_encode.wav"

    try:
        subprocess.run([
            FFMPEG_PATH, "-y", "-i", input_path, 
            "-vn", "-acodec", "pcm_s16le", temp_in_wav
        ], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        fs, data = wavfile.read(temp_in_wav)
        
        if len(data.shape) != 2 or data.shape[1] != 2:
            print("File is not stereo!")
            return False

        # float32 converting
        max_int = np.iinfo(data.dtype).max
        data = data.astype(np.float32) / max_int
        L = data[:, 0]
        R = data[:, 1]

        # Matrix decoding central and suround chanel
        C = (L + R) / np.sqrt(2)
        S = (L - R) / np.sqrt(2)

        C_binaural = C * 0.7 
        S_filtered = apply_lowpass(S, SURROUND_CUTOFF, fs)
        
        delay_samples = int(fs * (SURROUND_DELAY_MS / 1000.0))
        S_delayed = np.pad(S_filtered, (delay_samples, 0), 'constant')[:-delay_samples]

        S_L = S_delayed * 0.8
        S_R = -S_delayed * 0.8 

        Front_L = L * 0.6
        Front_R = R * 0.6

        New_L = Front_L + C_binaural + S_L
        New_R = Front_R + C_binaural + S_R

        # Normalization
        max_val = np.max(np.abs([New_L, New_R]))
        if max_val > 1.0:
            New_L /= max_val
            New_R /= max_val

        out_L = np.int16(New_L * 32767)
        out_R = np.int16(New_R * 32767)
        out_data = np.column_stack((out_L, out_R))

        wavfile.write(temp_out_wav, fs, out_data)

        # 4. ALAC converting with metadata and cover art preservation
        subprocess.run([
            FFMPEG_PATH, "-y", 
            "-i", temp_out_wav,      # Audio source
            "-i", input_path,        # Metadata and cover art source
            "-map", "0:a:0",         
            "-map", "1:v?",          
            "-map_metadata", "1",    
            "-c:a", "alac",          
            "-c:v", "copy",          
            output_path
        ], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        print(f"Ready! saved into: {output_path}")
        return True

    except subprocess.CalledProcessError:
        print("Error, FFMPEG not founded")
        return False
    except Exception as e:
        print(f"Error occurred: {e}")
        return False
    finally:
        if os.path.exists(temp_in_wav): os.remove(temp_in_wav)
        if os.path.exists(temp_out_wav): os.remove(temp_out_wav)

def main():
    if not os.path.exists(FFMPEG_PATH):
        print(f"\nError, file '{FFMPEG_PATH}' not founded!")
        return

    os.makedirs(INPUT_DIR, exist_ok=True)
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    
    # supported formats
    supported_formats = ('.m4a', '.alac', '.mp3', '.wav', '.flac', '.aac')
    
    files_to_process = [f for f in os.listdir(INPUT_DIR) if f.lower().endswith(supported_formats)]
    
    success_count = 0
    
    for filename in files_to_process:
        input_path = os.path.join(INPUT_DIR, filename)
        
        base_name = os.path.splitext(filename)[0]
        output_path = os.path.join(OUTPUT_DIR, f"{base_name}.m4a")
        
        if process_spatial_audio(input_path, output_path):
            success_count += 1
            
    
    print(f"Succesfuly coonverted: {success_count}/{len(files_to_process)}")
    
if __name__ == "__main__":
    main()