import pygetwindow as gw
import sys

# Set output to utf-8 just in case
if sys.stdout.encoding != 'utf-8':
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

def list_all_windows():
    print("--- LIST OF OPEN WINDOWS ---")
    try:
        windows = gw.getAllWindows()
        for win in windows:
            if win.title:
                try:
                    print(f"- {win.title}")
                except:
                    # Fallback for weird characters
                    print(f"- [UNREADABLE TITLE]")
    except Exception as e:
        print(f"Error: {e}")
    print("----------------------------")

if __name__ == "__main__":
    list_all_windows()
