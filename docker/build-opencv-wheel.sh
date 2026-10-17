#!/bin/sh
# Build the exact opencv-python source release against the existing FFmpeg
# prefix, applying the upstream OpenCV FFmpeg 9 compatibility change.
set -eu

out="$1"
patch_file="$2"
work=/tmp/opencv-build
prefix=/opt/ffmpeg9
build_venv=/tmp/opencv-build-venv
if [ -e "$work" ] || [ -e "$build_venv" ] || [ ! -e "$prefix/lib/pkgconfig" ]; then
    echo "OpenCV build paths are unavailable or FFmpeg development files are missing" >&2
    exit 1
fi
mkdir -p "$work" "$out"
trap 'rm -rf "$work" "$build_venv"' EXIT
cd "$work"

curl --fail --silent --show-error --location --proto '=https' --tlsv1.2 \
    --retry 3 --output opencv_python-5.0.0.93.tar.gz \
    https://files.pythonhosted.org/packages/79/4c/a438d23e09ce2033c09f7b784ad2fbdb0adf529e434101ed28f142226f98/opencv_python-5.0.0.93.tar.gz
echo '66aac3e5b5faa48d4025816592f3af19e4bfc2c68dec067bae2dbb4ca10aa9e2  opencv_python-5.0.0.93.tar.gz' | sha256sum --check --strict --quiet
echo 'ee7c145e51c7988325296ea2cc1dc70b42ecd6e27b49386370b926c2211ff6f4  '"$patch_file" | sha256sum --check --strict --quiet
tar -xzf opencv_python-5.0.0.93.tar.gz
(
    cd opencv_python-5.0.0.93/opencv
    patch --batch --forward -p1 < "$patch_file"
)

# The sdist's Python 3.14 build requirements need NumPy 2.3.2 and
# setuptools <70. Keep those old build inputs out of the application venv.
python -m venv "$build_venv"
"$build_venv/bin/python" -m pip install --no-cache-dir \
    'pip==26.2.1' 'setuptools==69.5.1' 'wheel==0.45.1' \
    'numpy==2.3.2' 'packaging==25.0' 'scikit-build==0.18.1' \
    'distro==1.9.0'

export OPENCV_PYTHON_SKIP_GIT_COMMANDS=1
export CMAKE_BUILD_PARALLEL_LEVEL="${CMAKE_BUILD_PARALLEL_LEVEL:-2}"
export MAKEFLAGS="-j${CMAKE_BUILD_PARALLEL_LEVEL}"
export PKG_CONFIG_PATH="$prefix/lib/pkgconfig"
export CFLAGS="-O2 -g0 -ffile-prefix-map=$work=."
export CXXFLAGS="$CFLAGS"
export LDFLAGS="-Wl,--disable-new-dtags,-rpath,$prefix/lib"
export CMAKE_ARGS="-DBUILD_LIST=core,imgproc,imgcodecs,videoio,features,calib,python3 -DWITH_FFMPEG=ON -DOPENCV_FFMPEG_ENABLE_LIBAVDEVICE=OFF -DWITH_IPP=OFF -DWITH_OPENCL=OFF -DWITH_GSTREAMER=OFF -DWITH_V4L=OFF -DWITH_GTK=OFF -DWITH_QT=OFF -DWITH_OBSENSOR=OFF -DBUILD_OPENEXR=OFF -DWITH_OPENEXR=OFF -DWITH_JPEG=OFF -DWITH_PNG=OFF -DWITH_TIFF=OFF -DWITH_WEBP=OFF -DWITH_OPENJPEG=OFF -DWITH_JASPER=OFF -DWITH_JPEGXL=OFF -DWITH_IMGCODEC_GIF=OFF -DWITH_IMGCODEC_HDR=OFF -DWITH_IMGCODEC_SUNRASTER=OFF -DWITH_IMGCODEC_PXM=OFF -DWITH_IMGCODEC_PFM=OFF -DWITH_PROTOBUF=OFF -DWITH_FLATBUFFERS=OFF -DCMAKE_INSTALL_RPATH=$prefix/lib -DCMAKE_SHARED_LINKER_FLAGS=-Wl,--disable-new-dtags"
"$build_venv/bin/python" -m pip wheel --no-build-isolation --no-deps \
    --no-cache-dir --wheel-dir "$out" ./opencv_python-5.0.0.93

# Keep only runtime FFmpeg libraries and license texts in later image layers.
rm -rf "$prefix/include" "$prefix/lib/pkgconfig" "$prefix/share" "$prefix/bin"
find "$prefix/lib" -maxdepth 1 -type l -name 'lib*.so' -delete
