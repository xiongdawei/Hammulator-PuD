FROM ubuntu:22.04 AS hammulator-build

ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update && apt-get install -y \
    build-essential \
    git \
    m4 \
    scons \
    zlib1g \
    zlib1g-dev \
    libprotobuf-dev \
    protobuf-compiler \
    libprotoc-dev \
    libgoogle-perftools-dev \
    python3-dev \
    libboost-all-dev \
    pkg-config \
    cmake \
    libinih-dev \
    genext2fs \
    mold

WORKDIR /root

FROM hammulator-build AS hammulator-interactive

# https://github.com/comsec-group/cascade-artifacts/blob/master/Dockerfile
RUN apt-get install -y zsh curl ripgrep fd-find bat fzf
RUN sh -c "$(curl -fsSL https://raw.githubusercontent.com/ohmyzsh/ohmyzsh/master/tools/install.sh)" "" --unattended
RUN git clone https://github.com/zsh-users/zsh-autosuggestions ${ZSH_CUSTOM:-~/.oh-my-zsh/custom}/plugins/zsh-autosuggestions
RUN git clone https://github.com/zsh-users/zsh-syntax-highlighting ${ZSH_CUSTOM:-~/.oh-my-zsh/custom}/plugins/zsh-syntax-highlighting
RUN sed -i 's/plugins=(git)/plugins=(git zsh-autosuggestions zsh-syntax-highlighting)/' /root/.zshrc

RUN git config --global user.email "you@example.com" && git config --global user.name "Your Name"
RUN echo 'echo -e "Do not forget to reconfigure your git name and mail with:\ngit config --global user.email \"you@example.com\" && git config --global user.name \"Your Name\""' >> /root/.zshrc

WORKDIR /root
CMD ["/usr/bin/zsh"]
