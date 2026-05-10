from kubernetes import client, config
from kubernetes.client.rest import ApiException
import uuid
import time

class SandboxExecutor:
    def __init__(self, namespace: str = "coding-agent"):
        try:
            config.load_incluster_config()
        except config.ConfigException:
            config.load_kube_config()
        self.namespace = namespace
        self.v1 = client.CoreV1Api()

    def start_sandbox(self, image: str = "ubuntu:22.04") -> str:
        sandbox_id = f"sandbox-{uuid.uuid4().hex[:8]}"
        pod = client.V1Pod(
            metadata=client.V1ObjectMeta(name=sandbox_id, labels={"app": "kodewriter-sandbox"}),
            spec=client.V1PodSpec(
                containers=[
                    client.V1Container(
                        name="sandbox",
                        image=image,
                        command=["/bin/sh", "-c", "sleep 3600"],
                        resources=client.V1ResourceRequirements(
                            requests={"cpu": "100m", "memory": "128Mi"},
                            limits={"cpu": "500m", "memory": "512Mi"}
                        )
                    )
                ],
                restart_policy="Never"
            )
        )
        try:
            self.v1.create_namespaced_pod(namespace=self.namespace, body=pod)
            return sandbox_id
        except ApiException as e:
            print(f"Exception when calling CoreV1Api->create_namespaced_pod: {e}")
            raise

    def execute_command(self, sandbox_id: str, command: str) -> str:
        # Placeholder for exec logic. Requires kubernetes.stream
        return f"Executing {command} in {sandbox_id}..."

    def stop_sandbox(self, sandbox_id: str):
        try:
            self.v1.delete_namespaced_pod(name=sandbox_id, namespace=self.namespace)
        except ApiException as e:
            print(f"Exception when calling CoreV1Api->delete_namespaced_pod: {e}")
