import os
import sys
import subprocess
from rich.console import Console

console = Console()

def setup_startup():
    project_dir = os.path.dirname(os.path.abspath(__file__))
    main_script = os.path.join(project_dir, "main.py")
    pythonw_path = sys.executable.replace("python.exe", "pythonw.exe")
    vbs_path = os.path.join(project_dir, "launcher.vbs")
    
    console.print("[bold cyan]THIET LAP CHE DO CHAY NGAM & STARTUP[/bold cyan]")

    # 1. Tạo file VBS để chạy ẩn console
    console.print(f"   - Tao launcher an: [green]{vbs_path}[/green]")
    vbs_content = f'Set WshShell = CreateObject("WScript.Shell")\n' \
                  f'WshShell.CurrentDirectory = "{project_dir}"\n' \
                  f'WshShell.Run "{pythonw_path} ""{main_script}""", 0\n' \
                  f'Set WshShell = Nothing'
    
    try:
        with open(vbs_path, "w", encoding="utf-16") as f: # VBS often likes UTF-16
            f.write(vbs_content)
    except Exception as e:
        console.print(f"[red]❌ Lỗi tạo file VBS: {e}[/red]")
        return

    # 2. Tạo Shortcut trong thư mục Startup của Windows
    startup_folder = os.path.join(os.environ['APPDATA'], r'Microsoft\Windows\Start Menu\Programs\Startup')
    shortcut_path = os.path.join(startup_folder, "DouyinTikTokBot.lnk")
    
    console.print(f"   - Tao shortcut Startup: [green]{shortcut_path}[/green]")
    
    ps_command = (
        f"$WshShell = New-Object -ComObject WScript.Shell; "
        f"$Shortcut = $WshShell.CreateShortcut('{shortcut_path}'); "
        f"$Shortcut.TargetPath = '{vbs_path}'; "
        f"$Shortcut.WorkingDirectory = '{project_dir}'; "
        f"$Shortcut.Description = 'Douyin to TikTok Automation Bot'; "
        f"$Shortcut.Save()"
    )
    
    try:
        subprocess.run(["powershell", "-Command", ps_command], check=True)
        console.print("[bold green]THANH CONG! Bot se tu khoi dong cung Windows o che do an.[/bold green]")
        console.print("[yellow]Luu y: Ban co the kiem tra Log trong file 'system.log' de xem trang thai chay ngam.[/yellow]")
    except Exception as e:
        console.print(f"[red]Loi tao Shortcut: {e}[/red]")

def remove_startup():
    startup_folder = os.path.join(os.environ['APPDATA'], r'Microsoft\Windows\Start Menu\Programs\Startup')
    shortcut_path = os.path.join(startup_folder, "DouyinTikTokBot.lnk")
    vbs_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "launcher.vbs")
    
    console.print("[bold yellow]DANG GO BO STARTUP...[/bold yellow]")
    
    try:
        if os.path.exists(shortcut_path):
            os.remove(shortcut_path)
            console.print("   - Da xoa shortcut Startup.")
        if os.path.exists(vbs_path):
            os.remove(vbs_path)
            console.print("   - Da xoa file launcher VBS.")
        console.print("[bold green]Da go bo thanh cong.[/bold green]")
    except Exception as e:
        console.print(f"[red]❌ Lỗi: {e}[/red]")

if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--remove":
        remove_startup()
    else:
        setup_startup()
