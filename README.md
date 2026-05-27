# PrinterUtility

PrinterUtility is a small Windows GUI utility for Epson ET-2820 ink pad counter service work.

It was built for repair/right-to-repair use: detect the printer, read its service status, reset the ET-2820 ink pad counter after confirmation, and guide the required power-cycle.

## Features

- Scans Epson USB devices.
- Reads Epson Device ID through the utility interface.
- Reads printer status and service error state.
- Reads ET-2820 waste-counter memory bytes.
- Shows a plain ink pad service summary plus technical byte view.
- Resets the ET-2820 ink pad counter after confirmation.
- Writes a reset backup to `runtime/backups`.
- After reset, watches USB until the printer is powered off and back on.
- Deletes the reset backup automatically only after the follow-up read confirms the lock is gone.

## Requirements

- Windows
- Python launcher (`py`) installed
- Epson ET-2820 connected over USB
- Administrator rights for USB service access

## Run

```bat
install.bat
run.bat
```

`run.bat` relaunches itself with administrator rights before starting the GUI.

## Clean Local Install

```bat
clean.bat
```

`clean.bat` removes the local `.venv` and Python cache files only.

## Safety Notes

Resetting the counter does not replace or clean the physical ink pad. Make sure the pad/waste ink path is physically safe before resetting.

This tool currently targets the Epson ET-2820. Do not use it on other models unless support is explicitly added and verified.

## License

This project is free and source-available for non-commercial repair, learning, and modification.

You may use, fork, edit, and redistribute it for non-commercial purposes, but you may not sell it or claim the original project as your own. Forks and modified versions must keep attribution and remain free/source-available under the included license.

See [LICENSE](LICENSE) for the full terms.
