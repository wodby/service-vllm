"""Render the service's value mappings against its pinned public chart, offline from Kubernetes."""
import copy
import os
from pathlib import Path
import re
import subprocess
import tempfile
import unittest

import yaml


def put(values, path, value):
    parts = path.split(".")
    for part in parts[:-1]:
        values = values.setdefault(part, {})
    values[parts[-1]] = value


class ContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.manifest = yaml.safe_load((Path(__file__).parents[1] / "service.yml").read_text())
        cls.temp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.temp.cleanup)
        cls.env = {**os.environ, "KUBECONFIG": os.devnull}
        helm = cls.manifest["helm"]
        subprocess.run(["helm", "pull", helm["chart"], "--version", str(helm["version"]),
                        "--untar", "--untardir", cls.temp.name], env=cls.env, check=True, capture_output=True)
        cls.chart = str(Path(cls.temp.name) / "stateless")

    def values(self, replicas=1, count=1):
        values = {}
        for entry in self.manifest["helm"]["values"]:
            put(values, entry["name"], copy.deepcopy(entry["value"]))
        mappings = self.manifest["helm"]["valueMappings"]
        put(values, mappings["replicas"], replicas)
        put(values, mappings["serviceAccountName"], "inference-account")
        put(values, mappings["serviceAccountCreate"], False)
        values.update(nameOverride="renamed-model", fullnameOverride="inference")
        values["image"] = {"registry": "docker.io", "repository": "vllm/vllm-openai", "tag": self.manifest["options"][0]["tag"], "pullSecrets": ["image-pull"]}
        values["envVars"] = [{"name": s["var"], "value": s["default"]} for s in self.manifest["settings"] if "default" in s]
        for entry in self.manifest["env"]:
            if entry.get("secret"):
                values["envVars"].append({"name": entry["name"], "valueFrom": {"secretKeyRef": {"name": "inference-secret", "key": "api_key"}}})
            else:
                values["envVars"].append({"name": entry["name"], "value": entry["value"]})
        values["resources"]["requests"]["nvidia.com/gpu"] = count
        values["resources"]["limits"]["nvidia.com/gpu"] = count
        policy = self.manifest["deployment"]
        values["strategy"] = {"type": "RollingUpdate", "rollingUpdate": {"maxSurge": policy["maxSurge"], "maxUnavailable": policy["maxUnavailable"]}}
        values.update(minReadySeconds=10, progressDeadlineSeconds=1800, terminationGracePeriodSeconds=120)
        return values

    def render(self, values):
        result = subprocess.run(["helm", "template", "inference", self.chart, "--namespace", "test", "-f", "-"],
                                input=yaml.safe_dump(values), text=True, env=self.env, check=True, capture_output=True)
        return sorted([doc for doc in yaml.safe_load_all(result.stdout) if doc], key=lambda doc: doc["kind"])

    def test_workload_mapping_and_gpu_overrides(self):
        for replicas, count in [(0, 0), (1, 1), (3, 2)]:
            with self.subTest(replicas=replicas, count=count):
                documents = self.render(self.values(replicas, count))
                self.assertEqual([doc["kind"] for doc in documents], ["Deployment", "Service"])
                self.assertEqual(documents[1]["spec"]["type"], "ClusterIP")
                self.assertEqual(documents[1]["spec"]["ports"][0]["port"], 8000)
                deployment = documents[0]
                self.assertEqual(deployment["spec"]["replicas"], replicas)
                self.assertEqual(deployment["metadata"]["labels"]["app.kubernetes.io/instance"], "inference")
                self.assertEqual(deployment["spec"]["selector"]["matchLabels"]["app.kubernetes.io/name"], "renamed-model")
                pod = deployment["spec"]["template"]["spec"]
                self.assertEqual(pod["serviceAccountName"], "inference-account")
                self.assertEqual(pod["imagePullSecrets"], [{"name": "image-pull"}])
                self.assertEqual(len(pod["containers"]), 1)
                container = pod["containers"][0]
                self.assertEqual(container["name"], self.manifest["workloads"][0]["containers"][0]["name"])
                self.assertEqual(container["image"], "docker.io/vllm/vllm-openai:v0.29.0")
                self.assertNotIn("command", container, "preserve the official image entrypoint")
                for key in ["requests", "limits"]:
                    self.assertEqual(container["resources"][key]["nvidia.com/gpu"], count)
                self.assertEqual(container["securityContext"]["runAsUser"], 2000)
                self.assertEqual({v["name"] for v in pod["volumes"]}, {m["name"] for m in container["volumeMounts"]})

    def test_settings_and_secrets(self):
        self.assertFalse(self.manifest["endpoints"][0]["ports"][0]["private"], "Application Access selects routable HTTP endpoints; the stack must require its private-network policy")
        container = self.render(self.values())[0]["spec"]["template"]["spec"]["containers"][0]
        variables = {item["name"] for item in container["env"]}
        environment = {item["name"]: item for item in container["env"]}
        self.assertEqual(environment["HF_HOME"]["value"], "/cache/huggingface")
        self.assertEqual(environment["VLLM_CACHE_ROOT"]["value"], "/cache/vllm")
        self.assertIn("secretKeyRef", environment["VLLM_API_KEY"]["valueFrom"])
        references = set(re.findall(r"\$\(([^)]+)\)", " ".join(container["args"])))
        self.assertLessEqual(references, variables)
        self.assertNotIn("--trust-remote-code", container["args"])
        self.assertNotIn("--api-key", container["args"])
        self.assertNotIn("--hf-token", container["args"])
        self.assertTrue(next(s for s in self.manifest["settings"] if s["name"] == "hf_token")["secret"])
        self.assertTrue(next(e for e in self.manifest["env"] if e["name"] == "VLLM_API_KEY")["secret"])
        self.assertEqual(self.manifest["workloads"][0]["containers"][0]["resources"]["gpu"], {"resourceName": "nvidia.com/gpu", "count": 1})

    def test_rollout_and_probes(self):
        deployment = self.render(self.values())[0]["spec"]
        self.assertEqual(deployment["strategy"]["rollingUpdate"], {"maxSurge": 0, "maxUnavailable": 1})
        pod = deployment["template"]["spec"]
        startup = pod["containers"][0]["startupProbe"]
        self.assertLess(startup["periodSeconds"] * startup["failureThreshold"], deployment["progressDeadlineSeconds"])
        self.assertEqual(pod["terminationGracePeriodSeconds"], 120)
        for name in ["startupProbe", "readinessProbe", "livenessProbe"]:
            self.assertEqual(pod["containers"][0][name]["httpGet"], {"path": "/health", "port": "http"})


if __name__ == "__main__":
    unittest.main()
