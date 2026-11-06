"""SER: valida Python 3.15.2+ 64 bits sem alterar o computador."""
import sys
import struct

compativel = sys.version_info[:2] == (3, 15) and sys.version_info >= (3, 15, 2) and struct.calcsize("P") == 8
if compativel and "--path" in sys.argv:
    print(sys.executable)
raise SystemExit(0 if compativel else 1)
