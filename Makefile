REGISTRY ?= docker.io
IMAGE ?= bborbe/teamvault
VERSION ?= 0.8.4

default: build

build:
	docker build --build-arg VERSION=$(VERSION) --no-cache --rm=true -t $(REGISTRY)/$(IMAGE):$(VERSION) .

upload:
	docker push $(REGISTRY)/$(IMAGE):$(VERSION)

clean:
	docker rmi $(REGISTRY)/$(IMAGE):$(VERSION)

