# th13 神灵庙行为还原移植 — 容器工作流入口
# 全部工作在 podman 容器内进行，不污染本地环境。
# 前置：monorepo 已克隆到 ./eagler-touhou（Dockerfile 随 ports/th13 入库，本地与 CI 共用）。
IMAGE   ?= localhost/th13-dev
IMAGE_CI ?= localhost/th13-ci
IMAGE_SHOT ?= localhost/th13-shot
PODMAN  ?= podman
DEV_UID ?= $(shell id -u)
DEV_GID ?= $(shell id -g)
CONTAINER_DIR = eagler-touhou/ports/th13/container
RUNFLAGS = --rm --userns=keep-id -v $(CURDIR):/work:Z -w /work
# dev 镜像目标追加 HOME 固定：杜绝工具把 XDG 配置写进工作区卷（.config/.local 污染）。
# shot/ci 镜像为 root 用户（无 dev 家目录），不覆盖。
DEV_RUNFLAGS = $(RUNFLAGS) -e HOME=/home/dev
PORT = /work/eagler-touhou/ports/th13

.PHONY: image image-ci image-shot shell exec exec-port ci shot extract inventory thtk thtk-clean help

help:
	@echo "目标:"
	@echo "  make image             构建 dev 镜像（全工具链）"
	@echo "  make image-ci          构建 ci 镜像（精简）"
	@echo "  make image-shot        构建 shot 镜像（chromium+playwright-core 无头取帧）"
	@echo "  make shell             进入交互式开发容器"
	@echo "  make exec CMD='…'      在容器内 /work 下执行单条命令"
	@echo "  make exec-port CMD='…' 在 ports/th13 目录下执行（构建/测试常用）"
	@echo "  make ci CMD='…'        在 ci 镜像内执行命令"
	@echo "  make shot CMD='…'      在 shot 镜像内执行命令（无头取帧）"
	@echo "  make inventory         清点 th13ogg.7z 内容（只读）"
	@echo "  make extract           解包原版到 games/th13/"
	@echo "  make thtk              临时克隆并编译 thtk 到 tools/thtk-tmp/"
	@echo "  make thtk-clean        清理 thtk 临时目录"

image:
	$(PODMAN) build --target dev -t $(IMAGE) \
		--build-arg DEV_UID=$(DEV_UID) --build-arg DEV_GID=$(DEV_GID) $(CONTAINER_DIR)/

image-ci:
	$(PODMAN) build --target ci -t $(IMAGE_CI) $(CONTAINER_DIR)/

image-shot:
	$(PODMAN) build --target shot -t $(IMAGE_SHOT) $(CONTAINER_DIR)/

shell:
	$(PODMAN) run $(DEV_RUNFLAGS) -it $(IMAGE)

exec:
	$(PODMAN) run $(DEV_RUNFLAGS) -i $(IMAGE) bash -lc 'cd /work && $(CMD)'

exec-port:
	$(PODMAN) run $(DEV_RUNFLAGS) -i $(IMAGE) bash -lc 'cd $(PORT) && $(CMD)'

ci:
	$(PODMAN) run $(RUNFLAGS) -i $(IMAGE_CI) bash -lc 'cd /work && $(CMD)'

shot:
	$(PODMAN) run $(RUNFLAGS) -i $(IMAGE_SHOT) bash -lc 'cd /work && $(CMD)'

inventory:
	$(PODMAN) run $(DEV_RUNFLAGS) -i $(IMAGE) bash -lc 'scripts/inventory.sh'

extract:
	$(PODMAN) run $(DEV_RUNFLAGS) -i $(IMAGE) bash -lc 'scripts/extract.sh'

thtk:
	$(PODMAN) run $(DEV_RUNFLAGS) -i $(IMAGE) bash -lc 'scripts/thtk-build.sh'

thtk-clean:
	$(PODMAN) run $(DEV_RUNFLAGS) -i $(IMAGE) bash -lc 'scripts/thtk-clean.sh'
