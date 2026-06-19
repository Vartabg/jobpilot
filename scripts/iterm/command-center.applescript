-- JobPilot Command Center — one window, full-screen HUD.
-- Layout: single pane (boot → interactive job search HUD)

property hudProfile : "JobPilot · Command Center"
property jpMarker : "JobPilot"

on jobPilotWindows()
	set hits to {}
	tell application "iTerm"
		repeat with w in windows
			set isJP to false
			repeat with t in tabs of w
				repeat with s in sessions of t
					try
						set p to profile name of s
						if p contains jpMarker then
							set isJP to true
							exit repeat
						end if
					end try
				end repeat
				if isJP then exit repeat
			end repeat
			if isJP then set end of hits to w
		end repeat
	end tell
	return hits
end jobPilotWindows

on isCommandCenter(w)
	tell application "iTerm"
		tell w
			tell current tab
				if (count of sessions) < 1 then return false
				try
					-- Current: single full-screen HUD pane
					set hudName to profile name of session 1
					if hudName is hudProfile then return true
					-- Legacy: 3-pane layout (middle session was HUD)
					if (count of sessions) >= 3 then
						set hudName to profile name of session 2
						if hudName is hudProfile then return true
					end if
				end try
			end tell
		end tell
	end tell
	return false
end isCommandCenter

on focusWindow(w)
	tell application "iTerm"
		activate
		set index of w to 1
		tell w
			select current tab
			try
				tell current tab
					if (count of sessions) >= 3 then
						select session 2
					else
						select session 1
					end if
				end tell
			end try
		end tell
	end tell
end focusWindow

on closeWindow(w)
	tell application "iTerm"
		try
			close w
		end try
	end tell
end closeWindow

on createCommandCenter()
	tell application "iTerm"
		activate
		reopen
		delay 0.3
		set w to create window with profile hudProfile
		set index of w to 1
		return w
	end tell
end createCommandCenter

on run argv
	set forceNew to false
	if (count of argv) > 0 then
		if item 1 of argv is "new" or item 1 of argv is "--new" then set forceNew to true
	end if

	set jpWins to jobPilotWindows()
	set ccWin to missing value

	if not forceNew then
		repeat with w in jpWins
			if isCommandCenter(w) then
				set ccWin to w
				exit repeat
			end if
		end repeat
	end if

	if ccWin is not missing value then
		repeat with w in jpWins
			if w is not ccWin then closeWindow(w)
		end repeat
		focusWindow(ccWin)
		return "focused"
	end if

	repeat with w in jpWins
		closeWindow(w)
	end repeat
	createCommandCenter()
	return "created"
end run