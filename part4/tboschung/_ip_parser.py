from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional


NODES_FILE = Path(__file__).with_name("_ip_nodes.txt")


@dataclass(frozen=True)
class K8sNode:
    name: str
    status: str
    role: str
    age: str
    version: str
    internal_ip: str
    external_ip: str
    os_image: str
    kernel_version: str
    container_runtime: str


class K8sNodeTable:
    def __init__(self, raw: str):
        self.nodes: List[K8sNode] = self._parse(raw)

    def _parse(self, raw: str) -> List[K8sNode]:
        nodes = []

        for line in raw.strip().splitlines():
            line = line.strip()

            if not line:
                continue

            # Ignore optional kubectl header line.
            if line.startswith("NAME "):
                continue

            tokens = line.split()

            if len(tokens) < 10:
                raise ValueError(f"Could not parse node line: {line!r}")

            name = tokens[0]
            status = tokens[1]
            role = tokens[2]
            age = tokens[3]
            version = tokens[4]
            internal_ip = tokens[5]
            external_ip = tokens[6]

            # OS image can contain spaces, so take everything between external IP
            # and the final two fields.
            os_image = " ".join(tokens[7:-2])
            kernel_version = tokens[-2]
            container_runtime = tokens[-1]

            nodes.append(
                K8sNode(
                    name=name,
                    status=status,
                    role=role,
                    age=age,
                    version=version,
                    internal_ip=internal_ip,
                    external_ip=external_ip,
                    os_image=os_image,
                    kernel_version=kernel_version,
                    container_runtime=container_runtime,
                )
            )

        return nodes

    def by_name(self, name: str) -> Optional[K8sNode]:
        return next((node for node in self.nodes if node.name == name), None)

    def by_prefix(self, prefix: str) -> Optional[K8sNode]:
        return next((node for node in self.nodes if node.name.startswith(prefix)), None)

    def require(self, name_or_prefix: str) -> K8sNode:
        node = self.by_name(name_or_prefix) or self.by_prefix(name_or_prefix)

        if node is None:
            known = ", ".join(node.name for node in self.nodes)
            raise KeyError(f"No node found for {name_or_prefix!r}. Known nodes: {known}")

        return node

    def internal_ip(self, name_or_prefix: str) -> str:
        return self.require(name_or_prefix).internal_ip

    def external_ip(self, name_or_prefix: str) -> str:
        return self.require(name_or_prefix).external_ip

    def all_internal_ips(self) -> Dict[str, str]:
        return {node.name: node.internal_ip for node in self.nodes}

    def all_external_ips(self) -> Dict[str, str]:
        return {node.name: node.external_ip for node in self.nodes}

    def all_ips(self) -> Dict[str, Dict[str, str]]:
        return {
            node.name: {
                "internal": node.internal_ip,
                "external": node.external_ip,
            }
            for node in self.nodes
        }


def load_nodes(path: Path = NODES_FILE) -> K8sNodeTable:
    if not path.exists():
        raise FileNotFoundError(
            f"Missing {path}. Copy your kubectl get nodes -o wide output into this file."
        )

    return K8sNodeTable(path.read_text())


NODES = load_nodes()

MEMCACHED_IP = NODES.external_ip("memcache-server")
MEASURE_IP = NODES.external_ip("client-measure")

INTERNAL_MEMCACHED_IP = NODES.internal_ip("memcache-server")
INTERNAL_AGENT_IP = NODES.internal_ip("client-agent")
INTERNAL_MEASURE_IP = NODES.internal_ip("client-measure")
INTERNAL_MASTER_IP = NODES.internal_ip("master")

AGENT_IP = NODES.external_ip("client-agent")
MASTER_IP = NODES.external_ip("master")