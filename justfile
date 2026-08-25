default:
  @just --list

gage_inc := "/home/gage/gage-linux-driver/Include/Public"
shim := "src/libgage_acq.so"

build:
  gcc -O2 -fPIC -shared \
    -I{{gage_inc}} \
    src/gage_acq.c \
    -o {{shim}} \
    -lCsSsm -lpthread \
    -Wl,-rpath,/usr/local/lib

clean:
  rm -f {{shim}} src/*.o

run: build
  python3 main.py