# 常用命令：make test / make run CASE=<uuid或名字> / make new NAME=xxx
PYTHON ?= python3
CASE ?= 用户数据质量
ARGS ?=

.PHONY: help test run all list new clean install

help:
	@echo "make install            安装依赖"
	@echo "make test               跑 pytest"
	@echo "make run CASE=<用例>    跑单个用例（可加 ARGS=...) 例如 make run CASE=bec36bd4 ARGS='--failed-rows'"
	@echo "make all                跑 testcase/ 下所有用例"
	@echo "make list               列出用例与规则"
	@echo "make new NAME=订单数据质量   生成新用例（可加 TYPE=odps CONN=odps_dw TABLE=ods_order_di）"
	@echo "make clean              清理 reports/ 与缓存"

install:
	$(PYTHON) -m pip install -r requirements.txt

test:
	$(PYTHON) -m pytest -q

run:
	$(PYTHON) main.py --case "$(CASE)" $(ARGS)

all:
	$(PYTHON) main.py --all $(ARGS)

list:
	$(PYTHON) main.py --list-cases
	$(PYTHON) main.py --case "$(CASE)" --list-rules

new:
	$(PYTHON) scripts/new_case.py --name "$(NAME)" --type $(or $(TYPE),csv) \
		$(if $(CONN),--conn $(CONN)) $(if $(TABLE),--table $(TABLE))

clean:
	rm -rf reports .pytest_cache
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
