-- Free Agents.app — the thin macOS launcher, compiled ON THIS MAC by install/gui-launcher.py.
-- It holds no Python and no copy of the plugin: Contents/Resources/locate.sh finds the installed
-- Free Agents, checks the GUI venv and starts the native window detached. A failure before the
-- window is ready (missing install, missing venv, crash at startup) arrives here as an error and
-- is shown as a native dialog — that works even when Python or NiceGUI never loaded.
on run
	set here to POSIX path of (path to me)
	set locator to quoted form of (here & "Contents/Resources/locate.sh")
	try
		do shell script "/bin/sh " & locator & " --launch"
	on error errMsg number errNum
		if errNum is -128 then return
		set logPath to (POSIX path of (path to library folder from user domain)) & "Logs/free-agents/gui.log"
		set choice to button returned of (display dialog errMsg with title "Free Agents" buttons {"Show Log", "OK"} default button "OK" with icon caution)
		if choice is "Show Log" then do shell script "/usr/bin/open -R " & quoted form of logPath & " || true"
	end try
end run
