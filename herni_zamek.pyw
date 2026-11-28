"""Game Lock Companion / Herní zámek – nejdřív učení, pak hry.

Spuštění:  pythonw herni_zamek.pyw
Test:      python herni_zamek.pyw --test --fake-time 22:40   (zrychlený čas, testovací data)
Kontrola:  python herni_zamek.pyw --selftest
"""
import traceback

from gamelock.app import log_error, main

if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception:
        log_error(traceback.format_exc())
        raise
