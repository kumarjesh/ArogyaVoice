import {
  db,
  collection,
  doc,
  setDoc,
  getDoc,
  updateDoc,
  onSnapshot,
  addDoc,
  getDocs,
} from "../firebase";

const configuration = {
  iceServers: [
    { urls: "stun:stun.l.google.com:19302" },
    { urls: "stun:stun1.l.google.com:19302" },
  ],
};

export class WebRTCVoiceCall {
  constructor(roomId, onRemoteStream, onCallEnded) {
    this.roomId = roomId;
    this.onRemoteStream = onRemoteStream;
    this.onCallEnded = onCallEnded;
    this.pc = null;
    this.localStream = null;
    this.unsubscribers = [];
  }

  async initLocalStream() {
    this.localStream = await navigator.mediaDevices.getUserMedia({
      audio: true,
      video: false,
    });
    return this.localStream;
  }

  async startCall(callerName = "Elder") {
    await this.initLocalStream();
    this.pc = new RTCPeerConnection(configuration);

    this.localStream.getTracks().forEach((track) => {
      this.pc.addTrack(track, this.localStream);
    });

    this.pc.ontrack = (event) => {
      if (event.streams && event.streams[0]) {
        this.onRemoteStream(event.streams[0]);
      }
    };

    const callDocRef = doc(db, "calls", this.roomId);
    const callerCandidatesCollection = collection(callDocRef, "callerCandidates");
    const calleeCandidatesCollection = collection(callDocRef, "calleeCandidates");

    this.pc.onicecandidate = (event) => {
      if (event.candidate) {
        addDoc(callerCandidatesCollection, event.candidate.toJSON());
      }
    };

    const offerDescription = await this.pc.createOffer();
    await this.pc.setLocalDescription(offerDescription);

    const offer = {
      sdp: offerDescription.sdp,
      type: offerDescription.type,
    };

    await setDoc(callDocRef, {
      offer,
      status: "calling",
      callerName,
      createdAt: Date.now(),
    });

    // Listen for answer
    const unsubCall = onSnapshot(callDocRef, (snapshot) => {
      const data = snapshot.data();
      if (!data) return;

      if (data.status === "ended") {
        this.endCall(false);
      } else if (!this.pc.currentRemoteDescription && data.answer) {
        const answerDescription = new RTCSessionDescription(data.answer);
        this.pc.setRemoteDescription(answerDescription);
      }
    });
    this.unsubscribers.push(unsubCall);

    // Listen for remote ICE candidates
    const unsubCandidates = onSnapshot(calleeCandidatesCollection, (snapshot) => {
      snapshot.docChanges().forEach((change) => {
        if (change.type === "added") {
          const candidate = new RTCIceCandidate(change.doc.data());
          this.pc.addIceCandidate(candidate).catch(() => {});
        }
      });
    });
    this.unsubscribers.push(unsubCandidates);
  }

  async answerCall() {
    await this.initLocalStream();
    this.pc = new RTCPeerConnection(configuration);

    this.localStream.getTracks().forEach((track) => {
      this.pc.addTrack(track, this.localStream);
    });

    this.pc.ontrack = (event) => {
      if (event.streams && event.streams[0]) {
        this.onRemoteStream(event.streams[0]);
      }
    };

    const callDocRef = doc(db, "calls", this.roomId);
    const callerCandidatesCollection = collection(callDocRef, "callerCandidates");
    const calleeCandidatesCollection = collection(callDocRef, "calleeCandidates");

    this.pc.onicecandidate = (event) => {
      if (event.candidate) {
        addDoc(calleeCandidatesCollection, event.candidate.toJSON());
      }
    };

    const callSnapshot = await getDoc(callDocRef);
    const callData = callSnapshot.data();
    if (!callData || !callData.offer) {
      throw new Error("Call offer not found");
    }

    await this.pc.setRemoteDescription(new RTCSessionDescription(callData.offer));

    const answerDescription = await this.pc.createAnswer();
    await this.pc.setLocalDescription(answerDescription);

    const answer = {
      type: answerDescription.type,
      sdp: answerDescription.sdp,
    };

    await updateDoc(callDocRef, { answer, status: "connected" });

    // Listen for call ending
    const unsubCall = onSnapshot(callDocRef, (snapshot) => {
      const data = snapshot.data();
      if (data && data.status === "ended") {
        this.endCall(false);
      }
    });
    this.unsubscribers.push(unsubCall);

    // Listen for caller ICE candidates
    const unsubCandidates = onSnapshot(callerCandidatesCollection, (snapshot) => {
      snapshot.docChanges().forEach((change) => {
        if (change.type === "added") {
          const candidate = new RTCIceCandidate(change.doc.data());
          this.pc.addIceCandidate(candidate).catch(() => {});
        }
      });
    });
    this.unsubscribers.push(unsubCandidates);
  }

  endCall(updateFirestore = true) {
    this.unsubscribers.forEach((unsub) => unsub());
    this.unsubscribers = [];

    if (this.localStream) {
      this.localStream.getTracks().forEach((track) => track.stop());
      this.localStream = null;
    }

    if (this.pc) {
      this.pc.close();
      this.pc = null;
    }

    if (updateFirestore && this.roomId) {
      const callDocRef = doc(db, "calls", this.roomId);
      updateDoc(callDocRef, { status: "ended" }).catch(() => {});
    }

    if (this.onCallEnded) {
      this.onCallEnded();
    }
  }
}
