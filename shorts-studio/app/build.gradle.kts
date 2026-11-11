plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
    id("org.jetbrains.kotlin.plugin.compose")
}
android {
    namespace = "com.shortsstudio.app"
    compileSdk = 35
    defaultConfig {
        applicationId = "com.shortsstudio.app.hd"
        minSdk = 26
        targetSdk = 35
        versionCode = 7
        versionName = "0.6.0"
        manifestPlaceholders["studioName"] = "Shorts Studio HD"
        manifestPlaceholders["pairScheme"] = "shortsstudiohd"
    }
    System.getenv("STUDIO_SIGNING_KEYSTORE")?.let { retainedKey ->
        signingConfigs.getByName("debug").storeFile = file(retainedKey)
    }
    buildTypes {
        release { isMinifyEnabled = false }
        create("director") {
            initWith(getByName("debug"))
            applicationIdSuffix = ".director"
            signingConfig = signingConfigs.getByName("debug")
            matchingFallbacks += "debug"
            manifestPlaceholders["studioName"] = "Shorts Studio Director"
            manifestPlaceholders["pairScheme"] = "shortsstudiodirector"
        }
    }
    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    kotlinOptions { jvmTarget = "17" }
    buildFeatures { compose = true }
}
dependencies {
    testImplementation("junit:junit:4.13.2")
    testImplementation("org.json:json:20240303")
    testImplementation("com.squareup.okhttp3:mockwebserver:4.12.0")
    val composeBom = platform("androidx.compose:compose-bom:2024.12.01")
    implementation(composeBom)
    implementation("androidx.compose.ui:ui")
    implementation("androidx.compose.ui:ui-tooling-preview")
    implementation("androidx.compose.material3:material3")
    implementation("androidx.compose.material:material-icons-extended")
    implementation("androidx.activity:activity-compose:1.9.3")
    implementation("androidx.core:core-ktx:1.15.0")
    implementation("androidx.work:work-runtime-ktx:2.10.0")
    implementation("org.jetbrains.kotlinx:kotlinx-coroutines-android:1.9.0")
    debugImplementation("androidx.compose.ui:ui-tooling")
}



