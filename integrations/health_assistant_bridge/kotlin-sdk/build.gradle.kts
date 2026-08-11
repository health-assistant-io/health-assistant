// Health Assistant Bridge — Kotlin SDK.
// Phase 1: kotlin("jvm") (consumed by app/android via composite includeBuild;
// also JVM-tested for HMAC parity). Converts to kotlin("multiplatform") with
// ios/android targets at Phase 9. Mirrors python-sdk/.../async_client.py +
// signing.py. See app/android/AGENTS.md "bridge wire contract".
plugins {
    kotlin("jvm") version "2.3.20"
    kotlin("plugin.serialization") version "2.3.20"
}

group = "io.healthassistant"
version = "0.2.0"

java {
    sourceCompatibility = JavaVersion.VERSION_11
    targetCompatibility = JavaVersion.VERSION_11
}

kotlin {
    compilerOptions {
        jvmTarget.set(org.jetbrains.kotlin.gradle.dsl.JvmTarget.JVM_11)
    }
}

dependencies {
    implementation("org.jetbrains.kotlinx:kotlinx-serialization-json:1.11.0")
    implementation("io.ktor:ktor-client-core:3.5.2")
    implementation("io.ktor:ktor-client-cio:3.5.2")

    testImplementation("io.ktor:ktor-client-mock:3.5.2")
    testImplementation("org.junit.jupiter:junit-jupiter:5.10.2")
    testImplementation(kotlin("test"))
}

tasks.test {
    useJUnitPlatform()
}
