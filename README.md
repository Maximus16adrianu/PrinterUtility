# PrinterUtility

CustomTkinter Epson ET-2820 service utility.

Current build:

- scans Epson USB devices
- reads Epson Device ID through the utility interface
- reads printer status
- reads ET-2820 waste-counter memory bytes
- shows a plain waste-counter summary and technical byte view
- can reset the ET-2820 ink pad counter after confirmation
- writes a reset backup to `runtime/backups`
- after reset, guides the power-cycle and watches USB until the printer returns
- deletes the reset backup automatically only after the follow-up read confirms the lock is gone

Run:

```bat
install.bat
run.bat
```

`run.bat` relaunches itself with administrator rights before starting the GUI.
`clean.bat` removes the local `.venv` and Python cache files only.
