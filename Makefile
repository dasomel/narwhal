.PHONY: lint validate test

lint:
	find scripts/ -name '*.sh' -print0 | xargs -0 shellcheck --severity=warning
	yamllint -d '{extends: default, rules: {line-length: {max: 200}, truthy: disable}}' gitops/apps/*.yaml gitops/resources/*.yaml

validate:
	ruby -c Vagrantfile
	@for f in gitops/apps/*.yaml gitops/resources/*.yaml; do \
		yq eval '.' "$$f" > /dev/null && echo "OK: $$f" || echo "FAIL: $$f"; \
	done

test:
	@echo "Run on cluster: vagrant ssh master-1 -c 'bash /home/vagrant/scripts/test/verify-cluster.sh'"
	@echo "Run SSO test: vagrant ssh master-1 -c 'bash /home/vagrant/scripts/test/test-sso.sh'"
