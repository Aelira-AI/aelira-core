# Research only: does not change the application's compiler or PDF profile.
FROM texlive/texlive@sha256:d1704cd87a8059ea1eb4bf06fd5097d0b0249f7e8eafd2d23eb3e50b7476ba20

ADD --checksum=sha256:f68d3c7af9989d448e87e49525358c7b9ae0c32376e8f3e71be9c73b0bca040f https://mirrors.ctan.org/systems/texlive/tlnet/archive/physics.tar.xz /opt/profile-packages/physics.tar.xz
ADD --checksum=sha256:c54ce936e1c2f0e88f32dd5f23056d46aa4c95094811abbf26661958b9c2ed37 https://mirrors.ctan.org/systems/texlive/tlnet/archive/siunitx.tar.xz /opt/profile-packages/siunitx.tar.xz
ADD --checksum=sha256:e5f09132bd59c58a17441516dc278dd52d3d0e0714135da9d294a7016b89cf25 https://mirrors.ctan.org/systems/texlive/tlnet/archive/titlesec.tar.xz /opt/profile-packages/titlesec.tar.xz
RUN tlmgr install --file /opt/profile-packages/physics.tar.xz /opt/profile-packages/siunitx.tar.xz /opt/profile-packages/titlesec.tar.xz
LABEL org.aelira.research.base="sha256:d1704cd87a8059ea1eb4bf06fd5097d0b0249f7e8eafd2d23eb3e50b7476ba20"
