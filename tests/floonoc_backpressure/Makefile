GVSOC_ROOT ?= ../../..
CASE ?= read_first
TARGET = test:write_first=$(if $(filter write_first,$(CASE)),true,false)
include $(GVSOC_ROOT)/core/tests/common.mk
