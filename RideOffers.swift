import SwiftUI
import Observation
import Foundation
import UniformTypeIdentifiers

// MARK: - Model
struct RideOffer: Identifiable {
    let id = UUID()
    let driverName: String
    let origin: String
    let destination: String
    let departureDateTime: Date
    let price: Decimal
    let availableSeats: Int
    let vehicleModel: String
    let plateSuffix: String
    var driverRating: Double? = nil
}

// MARK: - ViewModel
@Observable
class RideOfferListViewModel {
    // A autenticação futura deverá fornecer o nome do usuário da sessão.
    // Valor temporário para manter o protótipo utilizável enquanto o login não existe.
    var currentDriverName = "Carlos Silva"
    var offers: [RideOffer] = [
        RideOffer(
            driverName: "Carlos Silva",
            origin: "Centro",
            destination: "Polo Universitário de Macaé",
            departureDateTime: Date().addingTimeInterval(3600),
            price: 25.50,
            availableSeats: 3,
            vehicleModel: "Toyota Corolla",
            plateSuffix: "A1B2"
        ),
        RideOffer(
            driverName: "Ana Souza",
            origin: "Universidade",
            destination: "Polo Universitário de Macaé",
            departureDateTime: Date().addingTimeInterval(7200),
            price: 12.00,
            availableSeats: 2,
            vehicleModel: "Honda Civic",
            plateSuffix: "3C45"
        ),
        RideOffer(
            driverName: "João Pereira",
            origin: "Estação",
            destination: "Polo Universitário de Macaé",
            departureDateTime: Date().addingTimeInterval(1800),
            price: 8.75,
            availableSeats: 1,
            vehicleModel: "Chevrolet Onix",
            plateSuffix: "D6E7"
        )
    ]
    var passengerBookings: Set<UUID> = []
    var cancelledOfferIDs: Set<UUID> = []
    var passengerNotifications: [String] = []

    func addOffer(driverName: String, origin: String, departureDateTime: Date, price: Decimal, availableSeats: Int, vehicleModel: String, plateSuffix: String) {
        let offer = RideOffer(
            driverName: driverName,
            origin: origin,
            destination: "Polo Universitário de Macaé",
            departureDateTime: departureDateTime,
            price: price,
            availableSeats: availableSeats,
            vehicleModel: vehicleModel,
            plateSuffix: plateSuffix.uppercased()
        )
        offers.insert(offer, at: 0)
    }

    func book(_ offer: RideOffer) {
        guard !cancelledOfferIDs.contains(offer.id), offer.availableSeats > 0 else { return }
        passengerBookings.insert(offer.id)
    }

    func cancelPassengerBooking(_ offer: RideOffer) {
        passengerBookings.remove(offer.id)
    }

    func cancelDriverOffer(_ offer: RideOffer) {
        guard cancelledOfferIDs.insert(offer.id).inserted else { return }
        if passengerBookings.remove(offer.id) != nil {
            passengerNotifications.append("O motorista \(offer.driverName) cancelou a oferta de \(offer.origin) para o Polo Universitário de Macaé. Sua reserva foi cancelada.")
        }
    }
}

// MARK: - Ofertas para passageiros
struct RideOffersListView: View {
    let viewModel: RideOfferListViewModel
    @State private var bookingToCancel: RideOffer?
    @State private var popupMessage: String?

    var body: some View {
        NavigationStack {
            List(viewModel.offers.filter { !viewModel.cancelledOfferIDs.contains($0.id) }) { offer in
                VStack(alignment: .leading, spacing: 8) {
                    RideOfferRow(offer: offer)
                    if viewModel.passengerBookings.contains(offer.id) {
                        Button("Desistir da reserva", role: .destructive) { bookingToCancel = offer }
                    } else {
                        Button("Reservar vaga") { viewModel.book(offer) }
                            .disabled(offer.availableSeats == 0)
                    }
                }
            }
            .navigationTitle("Caronas disponíveis")
            .overlay {
                if viewModel.offers.isEmpty {
                    ContentUnavailableView("Nenhuma oferta", systemImage: "car", description: Text("As ofertas cadastradas aparecerão aqui."))
                }
            }
            .confirmationDialog("Deseja cancelar sua reserva?", isPresented: Binding(
                get: { bookingToCancel != nil }, set: { if !$0 { bookingToCancel = nil } }
            ), titleVisibility: .visible) {
                Button("Confirmar desistência", role: .destructive) {
                    if let offer = bookingToCancel { viewModel.cancelPassengerBooking(offer) }
                    bookingToCancel = nil
                }
                Button("Manter reserva", role: .cancel) { bookingToCancel = nil }
            } message: {
                Text("A vaga será liberada para outro passageiro.")
            }
            .onChange(of: viewModel.passengerNotifications.count) { _, _ in
                guard !viewModel.passengerNotifications.isEmpty else { return }
                popupMessage = viewModel.passengerNotifications.removeFirst()
            }
            .alert("Oferta cancelada pelo motorista", isPresented: Binding(
                get: { popupMessage != nil }, set: { if !$0 { popupMessage = nil } }
            )) {
                Button("OK", role: .cancel) { popupMessage = nil }
            } message: {
                Text(popupMessage ?? "")
            }
        }
    }
}

// MARK: - Cadastro de oferta pelo motorista
struct DriverOfferFormView: View {
    let viewModel: RideOfferListViewModel

    @State private var origin = ""
    @State private var departureDateTime = Date()
    @State private var priceText = ""
    @State private var availableSeats = 1
    @State private var vehicleModel = ""
    @State private var plateSuffix = ""
    @State private var showConfirmation = false
    @State private var offerToCancel: RideOffer?

    private var parsedPrice: Decimal? {
        let normalized = priceText.replacingOccurrences(of: ",", with: ".")
        guard let value = Double(normalized), value > 0 else { return nil }
        return Decimal(value)
    }

    private var canSubmit: Bool {
        !origin.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty &&
        !vehicleModel.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty &&
        plateSuffix.trimmingCharacters(in: .whitespacesAndNewlines).count == 4 &&
        parsedPrice != nil
    }

    var body: some View {
        NavigationStack {
            Form {
                Section("Motorista") {
                    LabeledContent("Oferta publicada por", value: viewModel.currentDriverName)
                    TextField("Ponto de partida (origem)", text: $origin)
                }

                Section("Veículo") {
                    TextField("Modelo do carro", text: $vehicleModel)
                    TextField("4 últimos caracteres da placa", text: $plateSuffix)
                        .textInputAutocapitalization(.characters)
                        .autocorrectionDisabled()
                        .onChange(of: plateSuffix) { _, newValue in
                            plateSuffix = String(newValue.uppercased().filter { $0.isLetter || $0.isNumber }.prefix(4))
                        }
                }

                Section("Viagem") {
                    LabeledContent("Destino", value: "Polo Universitário de Macaé")
                    DatePicker("Data e horário de partida", selection: $departureDateTime, in: Date()..., displayedComponents: [.date, .hourAndMinute])
                    TextField("Valor por passageiro (R$)", text: $priceText)
                        .keyboardType(.decimalPad)
                    Stepper("Vagas disponíveis: \(availableSeats)", value: $availableSeats, in: 1...4)
                }

                Section {
                    Button("Cadastrar oferta") {
                        guard let price = parsedPrice else { return }
                        viewModel.addOffer(
                            driverName: viewModel.currentDriverName,
                            origin: origin.trimmingCharacters(in: .whitespacesAndNewlines),
                            departureDateTime: departureDateTime,
                            price: price,
                            availableSeats: availableSeats,
                            vehicleModel: vehicleModel.trimmingCharacters(in: .whitespacesAndNewlines),
                            plateSuffix: plateSuffix
                        )
                        showConfirmation = true
                    }
                    .disabled(!canSubmit)
                }

                Section("Minhas ofertas") {
                    let ownOffers = viewModel.offers.filter {
                        $0.driverName == viewModel.currentDriverName && !viewModel.cancelledOfferIDs.contains($0.id)
                    }
                    if ownOffers.isEmpty {
                        Text("Você ainda não publicou ofertas.").foregroundStyle(.secondary)
                    } else {
                        ForEach(ownOffers) { offer in
                            VStack(alignment: .leading, spacing: 8) {
                                RideOfferRow(offer: offer)
                                Button("Desistir desta oferta", role: .destructive) { offerToCancel = offer }
                            }
                        }
                    }
                }
            }
            .navigationTitle("Cadastrar carona")
            .alert("Oferta cadastrada", isPresented: $showConfirmation) {
                Button("OK", role: .cancel) { }
            } message: {
                Text("Sua oferta já está disponível para os passageiros.")
            }
            .confirmationDialog("Deseja cancelar esta oferta?", isPresented: Binding(
                get: { offerToCancel != nil }, set: { if !$0 { offerToCancel = nil } }
            ), titleVisibility: .visible) {
                Button("Confirmar cancelamento", role: .destructive) {
                    if let offer = offerToCancel { viewModel.cancelDriverOffer(offer) }
                    offerToCancel = nil
                }
                Button("Manter oferta", role: .cancel) { offerToCancel = nil }
            } message: {
                Text("A oferta será removida da lista. Passageiros reservados serão avisados ao abrir a tela.")
            }
        }
    }
}

// MARK: - Cadastro do motorista
enum VerificationChannel: String, CaseIterable, Identifiable {
    case sms = "SMS"
    case whatsapp = "WhatsApp"

    var id: String { rawValue }
}

@MainActor
protocol PhoneVerificationService {
    func sendCode(to phone: String, via channel: VerificationChannel) async throws
    func verifyCode(_ code: String, for phone: String) async throws -> Bool
}

/// Bloqueia o fluxo até o serviço de verificação do backend ser conectado.
/// O código não é gerado nem validado no aplicativo.
private struct UnconfiguredPhoneVerificationService: PhoneVerificationService {
    func sendCode(to phone: String, via channel: VerificationChannel) async throws {
        throw VerificationServiceError.notConfigured
    }

    func verifyCode(_ code: String, for phone: String) async throws -> Bool {
        throw VerificationServiceError.notConfigured
    }
}

private enum VerificationServiceError: LocalizedError {
    case notConfigured

    var errorDescription: String? {
        "O serviço seguro de envio e validação ainda não foi conectado ao backend."
    }
}

// MARK: - Verificação de e-mail da conta
/// O backend deve criar um token aleatório, de uso único e com expiração de 24 horas,
/// persistir somente seu hash e enviar o link montado com esse token.
enum AccountVerificationEmailTemplate {
    static let subject = "Confirme seu e-mail para desbloquear sua conta"

    static func body(name: String, uniqueVerificationLink: URL) -> String {
        """
        Olá, \(name).

        Recebemos uma solicitação para criar sua conta. Para confirmar seu e-mail e desbloquear o acesso ao aplicativo, toque no link abaixo:

        Desbloquear minha conta: \(uniqueVerificationLink.absoluteString)

        Este link é pessoal, válido por 24 horas e pode ser usado uma única vez. Se você não solicitou a criação da conta, ignore esta mensagem.

        Por segurança, não compartilhe este link com ninguém.

        Equipe VaiProPolo
        """
    }
}

/// Implementação concreta deve chamar o backend e só liberar a conta após
/// o servidor confirmar o token recebido por link universal/app link.
@MainActor
protocol AccountVerificationService {
    func sendVerificationEmail(to email: String, name: String) async throws
    func confirmVerificationLink(_ url: URL) async throws -> Bool
}

private struct UnconfiguredAccountVerificationService: AccountVerificationService {
    func sendVerificationEmail(to email: String, name: String) async throws {
        throw VerificationServiceError.notConfigured
    }

    func confirmVerificationLink(_ url: URL) async throws -> Bool {
        throw VerificationServiceError.notConfigured
    }
}

struct EmailVerificationView: View {
    let email: String
    let name: String
    let service: any AccountVerificationService
    let onVerified: () -> Void

    @Environment(\.dismiss) private var dismiss
    @State private var isBusy = false
    @State private var isVerified = false
    @State private var emailSent = false
    @State private var errorMessage: String?

    var body: some View {
        NavigationStack {
            Form {
                Section("Confirmar e-mail") {
                    LabeledContent("E-mail", value: email)
                    Button(emailSent ? "Reenviar link de confirmação" : "Enviar link de confirmação") {
                        Task { await sendLink() }
                    }
                    .disabled(isBusy)

                    if emailSent {
                        Text("O link é único, pode ser usado uma vez e expira em 24 horas. Abra-o no dispositivo para desbloquear a conta.")
                            .font(.caption)
                            .foregroundStyle(.secondary)
                    }
                }
            }
            .navigationTitle("Desbloqueio da conta")
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("Fechar") { dismiss() }
                }
            }
            .onOpenURL { url in
                Task { await confirmLink(url) }
            }
            .onChange(of: isVerified) { _, verified in
                if verified {
                    onVerified()
                    dismiss()
                }
            }
            .alert("Não foi possível confirmar", isPresented: Binding(
                get: { errorMessage != nil },
                set: { if !$0 { errorMessage = nil } }
            )) {
                Button("OK", role: .cancel) { errorMessage = nil }
            } message: {
                Text(errorMessage ?? "")
            }
        }
    }

    @MainActor
    private func sendLink() async {
        isBusy = true
        defer { isBusy = false }
        do {
            try await service.sendVerificationEmail(to: email, name: name)
            emailSent = true
        } catch {
            errorMessage = error.localizedDescription
        }
    }

    @MainActor
    private func confirmLink(_ url: URL) async {
        isBusy = true
        defer { isBusy = false }
        do {
            isVerified = try await service.confirmVerificationLink(url)
            if !isVerified { errorMessage = "Link inválido, expirado ou já utilizado." }
        } catch {
            errorMessage = error.localizedDescription
        }
    }
}

struct PhoneVerificationView: View {
    let phone: String
    let service: any PhoneVerificationService
    let onVerified: () -> Void

    @Environment(\.dismiss) private var dismiss
    @State private var channel: VerificationChannel = .sms
    @State private var code = ""
    @State private var resendAvailableAt: Date?
    @State private var hasSentCode = false
    @State private var isBusy = false
    @State private var errorMessage: String?
    @State private var isVerified = false

    var body: some View {
        NavigationStack {
            Form {
                Section("Confirmar telefone") {
                    LabeledContent("Telefone", value: phone)
                    Picker("Enviar por", selection: $channel) {
                        ForEach(VerificationChannel.allCases) { option in
                            Text(option.rawValue).tag(option)
                        }
                    }

                    if hasSentCode, let resendAvailableAt {
                        TimelineView(.periodic(from: .now, by: 1)) { context in
                            let seconds = max(0, Int(ceil(resendAvailableAt.timeIntervalSince(context.date))))
                            VStack(alignment: .leading, spacing: 6) {
                                Button(seconds > 0 ? "Reenviar código (\(seconds / 60):\(String(format: "%02d", seconds % 60)))" : "Reenviar código") {
                                    Task { await sendCode() }
                                }
                                .disabled(isBusy || seconds > 0)
                                Text(seconds > 0 ? "O reenvio será liberado após 2 minutos." : "Já é possível pedir um novo código.")
                                    .font(.caption)
                                    .foregroundStyle(.secondary)
                            }
                        }
                    } else {
                        Button("Enviar código") {
                            Task { await sendCode() }
                        }
                        .disabled(isBusy)
                    }
                }

                Section("Código de confirmação") {
                    TextField("Código de 6 dígitos", text: $code)
                        .keyboardType(.numberPad)
                        .onChange(of: code) { _, value in
                            code = String(value.filter(\.isNumber).prefix(6))
                        }

                    Button("Confirmar código") {
                        Task { await verifyCode() }
                    }
                    .disabled(isBusy || code.count != 6 || !hasSentCode)
                }
            }
            .navigationTitle("Verificação de segurança")
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("Fechar") { dismiss() }
                }
            }
            .alert("Não foi possível verificar", isPresented: Binding(
                get: { errorMessage != nil },
                set: { if !$0 { errorMessage = nil } }
            )) {
                Button("OK", role: .cancel) { errorMessage = nil }
            } message: {
                Text(errorMessage ?? "")
            }
            .onChange(of: isVerified) { _, verified in
                if verified {
                    onVerified()
                    dismiss()
                }
            }
        }
    }

    private var secondsUntilResend: Int {
        guard let resendAvailableAt else { return 0 }
        return max(0, Int(ceil(resendAvailableAt.timeIntervalSinceNow)))
    }

    @MainActor
    private func sendCode() async {
        guard secondsUntilResend == 0 else { return }
        isBusy = true
        defer { isBusy = false }
        do {
            try await service.sendCode(to: phone, via: channel)
            hasSentCode = true
            code = ""
            resendAvailableAt = Date().addingTimeInterval(120)
        } catch {
            errorMessage = error.localizedDescription
        }
    }

    @MainActor
    private func verifyCode() async {
        isBusy = true
        defer { isBusy = false }
        do {
            isVerified = try await service.verifyCode(code, for: phone)
            if !isVerified { errorMessage = "Código inválido ou expirado. Confira e tente novamente." }
        } catch {
            errorMessage = error.localizedDescription
        }
    }
}

struct PassengerRegistrationView: View {
    @State private var cpf = ""
    @State private var fullName = ""
    @State private var email = ""
    @State private var phone = ""
    @State private var isShowingPhoneVerification = false
    @State private var isPhoneVerified = false
    @State private var isShowingEmailVerification = false
    @State private var isEmailVerified = false
    @State private var showConfirmation = false

    private var isValidCPF: Bool {
        let digits = cpf.filter(\.isNumber).compactMap { $0.wholeNumberValue }
        guard digits.count == 11, Set(digits).count > 1 else { return false }

        func checkDigit(for digits: [Int], startingWeight: Int) -> Int {
            let sum = digits.enumerated().reduce(0) { partial, item in
                partial + item.element * (startingWeight - item.offset)
            }
            let remainder = sum % 11
            return remainder < 2 ? 0 : 11 - remainder
        }

        let firstCheckDigit = checkDigit(for: Array(digits.prefix(9)), startingWeight: 10)
        guard digits[9] == firstCheckDigit else { return false }
        return digits[10] == checkDigit(for: Array(digits.prefix(10)), startingWeight: 11)
    }

    private var canSubmit: Bool {
        isValidCPF &&
        !fullName.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty &&
        email.contains("@") && isEmailVerified &&
        !phone.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty &&
        isPhoneVerified
    }

    var body: some View {
        NavigationStack {
            Form {
                Section("Dados pessoais") {
                    TextField("CPF", text: $cpf)
                        .keyboardType(.numberPad)
                        .onChange(of: cpf) { _, value in
                            cpf = String(value.filter(\.isNumber).prefix(11))
                        }
                    TextField("Nome completo", text: $fullName)
                        .textContentType(.name)
                    TextField("E-mail", text: $email)
                        .keyboardType(.emailAddress)
                        .textContentType(.emailAddress)
                        .textInputAutocapitalization(.never)
                        .autocorrectionDisabled()
                        .onChange(of: email) { _, _ in isEmailVerified = false }
                    Button(isEmailVerified ? "E-mail confirmado" : "Confirmar e-mail") {
                        isShowingEmailVerification = true
                    }
                    .disabled(!email.contains("@") || isEmailVerified)
                    TextField("Telefone", text: $phone)
                        .keyboardType(.phonePad)
                        .textContentType(.telephoneNumber)
                        .onChange(of: phone) { _, _ in isPhoneVerified = false }
                    Button(isPhoneVerified ? "Telefone confirmado" : "Verificar telefone") {
                        isShowingPhoneVerification = true
                    }
                    .disabled(phone.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty || isPhoneVerified)
                }

                Section {
                    Button("Cadastrar passageiro") {
                        showConfirmation = true
                    }
                    .disabled(!canSubmit)
                } footer: {
                    Text("O CPF precisa ser válido e o telefone confirmado para concluir o cadastro.")
                }
            }
            .navigationTitle("Cadastro do passageiro")
            .sheet(isPresented: $isShowingPhoneVerification) {
                PhoneVerificationView(
                    phone: phone,
                    service: UnconfiguredPhoneVerificationService(),
                    onVerified: { isPhoneVerified = true }
                )
            }
            .sheet(isPresented: $isShowingEmailVerification) {
                EmailVerificationView(
                    email: email,
                    name: fullName,
                    service: UnconfiguredAccountVerificationService(),
                    onVerified: { isEmailVerified = true }
                )
            }
            .alert("Cadastro concluído", isPresented: $showConfirmation) {
                Button("OK", role: .cancel) { }
            } message: {
                Text("Os dados do passageiro foram preenchidos. O envio e armazenamento serão conectados ao sistema de cadastro.")
            }
        }
    }
}

struct DriverRegistrationView: View {
    @State private var cpf = ""
    @State private var fullName = ""
    @State private var email = ""
    @State private var phone = ""
    @State private var carMake = ""
    @State private var carModel = ""
    @State private var fullPlate = ""
    @State private var cnhFileName: String?
    @State private var isSelectingCNH = false
    @State private var isShowingPhoneVerification = false
    @State private var isPhoneVerified = false
    @State private var isShowingEmailVerification = false
    @State private var isEmailVerified = false
    @State private var showConfirmation = false
    @State private var errorMessage: String?

    private var isValidCPF: Bool {
        let digits = cpf.filter(\.isNumber).compactMap { $0.wholeNumberValue }
        guard digits.count == 11,
              Set(digits).count > 1 else { return false }

        func checkDigit(for digits: [Int], startingWeight: Int) -> Int {
            let sum = digits.enumerated().reduce(0) { partial, item in
                partial + item.element * (startingWeight - item.offset)
            }
            let remainder = sum % 11
            return remainder < 2 ? 0 : 11 - remainder
        }

        let firstNine = Array(digits.prefix(9))
        let firstCheckDigit = checkDigit(for: firstNine, startingWeight: 10)
        guard digits[9] == firstCheckDigit else { return false }

        let firstTen = Array(digits.prefix(10))
        let secondCheckDigit = checkDigit(for: firstTen, startingWeight: 11)
        return digits[10] == secondCheckDigit
    }

    private var canSubmit: Bool {
        isValidCPF &&
        !fullName.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty &&
        email.contains("@") && isEmailVerified &&
        !phone.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty && isPhoneVerified &&
        !carMake.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty &&
        !carModel.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty &&
        fullPlate.count == 7 && cnhFileName != nil
    }

    var body: some View {
        NavigationStack {
            Form {
                Section("Dados pessoais") {
                    TextField("CPF", text: $cpf)
                        .keyboardType(.numberPad)
                        .onChange(of: cpf) { _, value in
                            cpf = String(value.filter(\.isNumber).prefix(11))
                        }
                    TextField("Nome completo", text: $fullName)
                        .textContentType(.name)
                    TextField("E-mail", text: $email)
                        .keyboardType(.emailAddress)
                        .textContentType(.emailAddress)
                        .textInputAutocapitalization(.never)
                        .autocorrectionDisabled()
                        .onChange(of: email) { _, _ in isEmailVerified = false }
                    Button(isEmailVerified ? "E-mail confirmado" : "Confirmar e-mail") {
                        isShowingEmailVerification = true
                    }
                    .disabled(!email.contains("@") || isEmailVerified)
                    TextField("Telefone", text: $phone)
                        .keyboardType(.phonePad)
                        .textContentType(.telephoneNumber)
                        .onChange(of: phone) { _, _ in isPhoneVerified = false }
                    Button(isPhoneVerified ? "Telefone confirmado" : "Verificar telefone") {
                        isShowingPhoneVerification = true
                    }
                    .disabled(phone.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty || isPhoneVerified)
                }

                Section("Veículo") {
                    TextField("Marca do carro", text: $carMake)
                    TextField("Modelo do carro", text: $carModel)
                    TextField("Placa completa", text: $fullPlate)
                        .textInputAutocapitalization(.characters)
                        .autocorrectionDisabled()
                        .onChange(of: fullPlate) { _, value in
                            fullPlate = String(value.uppercased().filter { $0.isLetter || $0.isNumber }.prefix(7))
                        }
                }

                Section("Carteira de habilitação") {
                    Button {
                        isSelectingCNH = true
                    } label: {
                        Label(cnhFileName ?? "Selecionar CNH em PDF", systemImage: "doc.badge.plus")
                    }
                    if let cnhFileName {
                        Text("Arquivo selecionado: \(cnhFileName)")
                            .font(.caption)
                            .foregroundStyle(.secondary)
                    }
                }

                Section {
                    Button("Cadastrar motorista") {
                        showConfirmation = true
                    }
                    .disabled(!canSubmit)
                }
            }
            .navigationTitle("Cadastro do motorista")
            .sheet(isPresented: $isShowingPhoneVerification) {
                PhoneVerificationView(
                    phone: phone,
                    service: UnconfiguredPhoneVerificationService(),
                    onVerified: { isPhoneVerified = true }
                )
            }
            .sheet(isPresented: $isShowingEmailVerification) {
                EmailVerificationView(
                    email: email,
                    name: fullName,
                    service: UnconfiguredAccountVerificationService(),
                    onVerified: { isEmailVerified = true }
                )
            }
            .fileImporter(isPresented: $isSelectingCNH, allowedContentTypes: [.pdf], allowsMultipleSelection: false) { result in
                switch result {
                case .success(let urls):
                    cnhFileName = urls.first?.lastPathComponent
                    errorMessage = nil
                case .failure(let error):
                    errorMessage = "Não foi possível selecionar o PDF: \(error.localizedDescription)"
                }
            }
            .alert("Cadastro concluído", isPresented: $showConfirmation) {
                Button("OK", role: .cancel) { }
            } message: {
                Text("Os dados do motorista foram preenchidos. O envio e armazenamento serão conectados ao sistema de cadastro.")
            }
            .alert("Erro ao selecionar arquivo", isPresented: Binding(
                get: { errorMessage != nil },
                set: { if !$0 { errorMessage = nil } }
            )) {
                Button("OK", role: .cancel) { errorMessage = nil }
            } message: {
                Text(errorMessage ?? "")
            }
        }
    }
}

// MARK: - Linha da oferta exibida ao passageiro
struct RideOfferRow: View {
    let offer: RideOffer

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack {
                Text(offer.driverName).font(.headline)
                Spacer()
                Text(offer.price, format: .currency(code: "BRL"))
                    .font(.headline)
                    .foregroundStyle(.blue)
            }

            Label("\(offer.origin) → \(offer.destination)", systemImage: "mappin.and.ellipse")
                .font(.subheadline)
                .foregroundStyle(.secondary)

            HStack {
                Label(offer.departureDateTime.formatted(date: .abbreviated, time: .shortened), systemImage: "calendar")
                Spacer()
                Label("\(offer.availableSeats) \(offer.availableSeats == 1 ? "vaga" : "vagas") restantes", systemImage: "person.2")
                    .foregroundStyle(offer.availableSeats > 0 ? .green : .red)
            }
            .font(.caption)

            Label("\(offer.vehicleModel) · placa ••••\(offer.plateSuffix)", systemImage: "car.side")
                .font(.caption)
                .foregroundStyle(.secondary)
            RatingStarsView(average: offer.driverRating, subject: "Motorista")
            EmergencyCallButton()
        }
        .padding(.vertical, 8)
    }
}

private struct RatingStarsView: View {
    let average: Double?
    let subject: String

    var body: some View {
        HStack(spacing: 5) {
            Image(systemName: "star.fill")
                .foregroundStyle(.orange)
            if let average {
                Text("\(subject): \(average.formatted(.number.precision(.fractionLength(1)))) / 5")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            } else {
                Text("\(subject): ainda sem avaliações")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
        }
        .accessibilityElement(children: .combine)
    }
}

private struct EmergencyCallButton: View {
    @Environment(\.openURL) private var openURL
    @State private var showFirstConfirmation = false
    @State private var showFinalConfirmation = false

    var body: some View {
        Button(role: .destructive) {
            showFirstConfirmation = true
        } label: {
            Label("Perigo na viagem · 190", systemImage: "exclamationmark.triangle.fill")
                .font(.caption.weight(.semibold))
        }
        .buttonStyle(.borderedProminent)
        .tint(.red)
        .confirmationDialog("Precisa de ajuda policial?", isPresented: $showFirstConfirmation, titleVisibility: .visible) {
            Button("Continuar para confirmação final", role: .destructive) {
                showFinalConfirmation = true
            }
            Button("Cancelar", role: .cancel) { }
        } message: {
            Text("Esta ação abrirá a ligação para a Polícia Militar pelo número 190. Use em caso de emergência.")
        }
        .confirmationDialog("Confirmação final: ligar para 190?", isPresented: $showFinalConfirmation, titleVisibility: .visible) {
            Button("Ligar para 190", role: .destructive) {
                if let emergencyURL = URL(string: "tel:190") {
                    openURL(emergencyURL)
                }
            }
            Button("Cancelar", role: .cancel) { }
        } message: {
            Text("Confirme somente se você estiver em perigo e precisar acionar a polícia.")
        }
    }
}

private struct BackendRating: Decodable {
    let starsAverage: Double?
    let reviewCount: Int
    let ratedTrips: Int
}

private struct BackendAccount: Decodable {
    let id: String
    let role: String
    let fullName: String
}

private struct BackendLoginResponse: Decodable {
    let accessToken: String
    let account: BackendAccount
}

private struct BackendErrorResponse: Decodable {
    let error: String
}

private struct BackendOffersResponse: Decodable {
    let offers: [BackendOffer]
}

private struct BackendOffer: Decodable, Identifiable {
    let id: String
    let driverId: String
    let driverName: String
    let origin: String
    let destination: String
    let departureAt: String
    let price: Double
    let availableSeats: Int
    let carMake: String?
    let carModel: String?
    let plateSuffix: String?
    let driverRating: BackendRating?
}

private struct BackendBookingsResponse: Decodable {
    let bookings: [BackendBooking]
}

private struct BackendBooking: Decodable, Identifiable {
    var id: String { bookingId }
    let bookingId: String
    let status: String
    let origin: String
    let destination: String
    let departureAt: String
    let price: Double
    let driverId: String
    let driverName: String
    let carMake: String?
    let carModel: String?
    let plateSuffix: String?
    let canRate: Bool
    let hasRated: Bool
    let driverRating: BackendRating
}

private struct BackendTripsResponse: Decodable {
    let trips: [BackendTrip]
}

private struct BackendTrip: Decodable, Identifiable {
    var id: String { bookingId }
    let bookingId: String
    let status: String
    let passengerId: String
    let passengerName: String
    let origin: String
    let destination: String
    let departureAt: String
    let price: Double
    let canRate: Bool
    let hasRated: Bool
    let passengerRating: BackendRating
}

private struct BackendMyOffersResponse: Decodable {
    let offers: [BackendOwnedOffer]
}

private struct BackendNotificationsResponse: Decodable {
    let notifications: [BackendNotification]
}

private struct BackendNotification: Decodable, Identifiable {
    let id: String
    let message: String
}

private struct BackendOwnedOffer: Decodable, Identifiable {
    let id: String
    let origin: String
    let destination: String
    let departureAt: String
    let price: Double
    let availableSeats: Int
    let totalSeats: Int
    let status: String
}

@MainActor
@Observable
private final class VaiProPoloBackend {
    var baseURL = "https://api.example.com"
    private(set) var accessToken: String?
    private(set) var account: BackendAccount?

    var isAuthenticated: Bool { accessToken != nil }

    func login(email: String, password: String) async throws {
        let data = try await send(
            path: "/v1/auth/login",
            method: "POST",
            body: ["email": email, "password": password]
        )
        let response = try decoder().decode(BackendLoginResponse.self, from: data)
        accessToken = response.accessToken
        account = response.account
    }

    func logout() async {
        do { _ = try await send(path: "/v1/auth/logout", method: "POST") } catch { }
        accessToken = nil
        account = nil
    }

    func fetchOffers() async throws -> [BackendOffer] {
        try decoder().decode(BackendOffersResponse.self, from: await send(path: "/v1/offers")).offers
    }

    func fetchBookings() async throws -> [BackendBooking] {
        try decoder().decode(BackendBookingsResponse.self, from: await send(path: "/v1/me/bookings")).bookings
    }

    func fetchDriverTrips() async throws -> [BackendTrip] {
        try decoder().decode(BackendTripsResponse.self, from: await send(path: "/v1/me/trips")).trips
    }

    func fetchMyOffers() async throws -> [BackendOwnedOffer] {
        try decoder().decode(BackendMyOffersResponse.self, from: await send(path: "/v1/me/offers")).offers
    }

    func fetchNotifications() async throws -> [BackendNotification] {
        try decoder().decode(BackendNotificationsResponse.self, from: await send(path: "/v1/me/notifications")).notifications
    }

    func markNotificationRead(id: String) async throws {
        _ = try await send(path: "/v1/notifications/\(id)/read", method: "POST", body: ["confirm": true])
    }

    func book(offerID: String) async throws {
        _ = try await send(path: "/v1/offers/\(offerID)/book", method: "POST")
    }

    func cancelBooking(id: String) async throws {
        _ = try await send(path: "/v1/bookings/\(id)", method: "DELETE", body: ["confirm": true])
    }

    func cancelOffer(id: String) async throws {
        _ = try await send(path: "/v1/offers/\(id)", method: "DELETE", body: ["confirm": true])
    }

    func createOffer(origin: String, departureAt: Date, price: Double, seats: Int) async throws {
        let formatter = ISO8601DateFormatter()
        _ = try await send(path: "/v1/offers", method: "POST", body: [
            "origin": origin,
            "departure_at": formatter.string(from: departureAt),
            "price": price,
            "available_seats": seats
        ])
    }

    func submitRating(bookingID: String, stars: Int) async throws {
        _ = try await send(path: "/v1/bookings/\(bookingID)/ratings", method: "POST", body: ["stars": stars])
    }

    private func send(path: String, method: String = "GET", body: [String: Any]? = nil) async throws -> Data {
        let server = baseURL.trimmingCharacters(in: .whitespacesAndNewlines).trimmingCharacters(in: CharacterSet(charactersIn: "/"))
        guard let url = URL(string: server + path), let scheme = url.scheme, ["https", "http"].contains(scheme) else {
            throw BackendConnectionError.invalidURL
        }
        var request = URLRequest(url: url)
        request.httpMethod = method
        request.setValue("application/json", forHTTPHeaderField: "Accept")
        if let accessToken {
            request.setValue("Bearer \(accessToken)", forHTTPHeaderField: "Authorization")
        }
        if let body {
            request.setValue("application/json", forHTTPHeaderField: "Content-Type")
            request.httpBody = try JSONSerialization.data(withJSONObject: body)
        }
        let (data, response) = try await URLSession.shared.data(for: request)
        guard let httpResponse = response as? HTTPURLResponse else { throw BackendConnectionError.invalidResponse }
        guard (200..<300).contains(httpResponse.statusCode) else {
            let backendError = try? decoder().decode(BackendErrorResponse.self, from: data)
            throw BackendConnectionError.requestFailed(backendError?.error ?? "Falha na comunicação com o servidor (HTTP \(httpResponse.statusCode)).")
        }
        return data
    }

    private func decoder() -> JSONDecoder {
        let decoder = JSONDecoder()
        decoder.keyDecodingStrategy = .convertFromSnakeCase
        return decoder
    }
}

private enum BackendConnectionError: LocalizedError {
    case invalidURL
    case invalidResponse
    case requestFailed(String)

    var errorDescription: String? {
        switch self {
        case .invalidURL: "Informe a URL HTTPS válida da API VaiProPolo."
        case .invalidResponse: "O servidor retornou uma resposta inválida."
        case .requestFailed(let message): message
        }
    }
}

private struct StarRatingPicker: View {
    @Binding var selection: Int

    var body: some View {
        HStack {
            ForEach(1...5, id: \.self) { star in
                Button {
                    selection = star
                } label: {
                    Image(systemName: star <= selection ? "star.fill" : "star")
                        .foregroundStyle(.orange)
                        .font(.title3)
                }
                .buttonStyle(.plain)
                .accessibilityLabel("\(star) \(star == 1 ? "estrela" : "estrelas")")
            }
        }
    }
}

private struct BackendConnectionView: View {
    @Bindable var api: VaiProPoloBackend
    @State private var email = ""
    @State private var password = ""
    @State private var origin = ""
    @State private var departureAt = Date().addingTimeInterval(3600)
    @State private var price = ""
    @State private var seats = 1
    @State private var offers: [BackendOffer] = []
    @State private var bookings: [BackendBooking] = []
    @State private var trips: [BackendTrip] = []
    @State private var myOffers: [BackendOwnedOffer] = []
    @State private var notifications: [BackendNotification] = []
    @State private var activeNotification: BackendNotification?
    @State private var selectedRatings: [String: Int] = [:]
    @State private var bookingToCancel: BackendBooking?
    @State private var offerToCancel: BackendOwnedOffer?
    @State private var isBusy = false
    @State private var errorMessage: String?
    @State private var infoMessage: String?

    var body: some View {
        NavigationStack {
            Form {
                if !api.isAuthenticated {
                    Section("Conexão com o backend") {
                        TextField("URL HTTPS da API", text: $api.baseURL)
                            .keyboardType(.URL)
                            .textInputAutocapitalization(.never)
                            .autocorrectionDisabled()
                        TextField("E-mail cadastrado", text: $email)
                            .keyboardType(.emailAddress)
                            .textInputAutocapitalization(.never)
                            .autocorrectionDisabled()
                        SecureField("Senha", text: $password)
                        Button("Entrar e carregar viagens") { Task { await signIn() } }
                            .disabled(isBusy || email.isEmpty || password.isEmpty)
                        Text("Use uma conta já cadastrada e confirmada no backend. O token fica apenas na memória desta sessão.")
                            .font(.caption)
                            .foregroundStyle(.secondary)
                    }
                } else if let account = api.account {
                        Section("Sessão") {
                        LabeledContent("Usuário", value: account.fullName)
                        LabeledContent("Perfil", value: account.role == "driver" ? "Motorista" : "Passageiro")
                        Button("Atualizar dados") { Task { await refresh() } }
                            .disabled(isBusy)
                        Button("Sair", role: .destructive) { Task { await api.logout(); clearData() } }
                    }

                    if account.role == "passenger", !notifications.isEmpty {
                        Section("Avisos") {
                            ForEach(notifications) { notification in
                                Label(notification.message, systemImage: "bell.badge")
                                    .onTapGesture { Task { await dismissNotification(notification) } }
                            }
                        }
                    }

                    if account.role == "passenger" {
                        Section("Ofertas disponíveis") {
                            if offers.isEmpty {
                                Text("Não há ofertas disponíveis.").foregroundStyle(.secondary)
                            }
                            ForEach(offers) { offer in
                                VStack(alignment: .leading, spacing: 8) {
                                    Text(offer.driverName).font(.headline)
                                    RatingStarsView(average: offer.driverRating?.starsAverage, subject: "Motorista")
                                    Text("\(offer.origin) → \(offer.destination)")
                                    Text(offer.departureAt)
                                    Text(offer.price, format: .currency(code: "BRL"))
                                    Label("\(offer.availableSeats) vagas restantes", systemImage: "person.2")
                                    Button("Reservar vaga") { Task { await perform { try await api.book(offerID: offer.id) } } }
                                    EmergencyCallButton()
                                }
                                .padding(.vertical, 6)
                            }
                        }
                        Section("Minhas viagens") {
                            if bookings.isEmpty { Text("Suas reservas aparecerão aqui.").foregroundStyle(.secondary) }
                            ForEach(bookings) { booking in
                                VStack(alignment: .leading, spacing: 8) {
                                    Text("Motorista: \(booking.driverName)").font(.headline)
                                    RatingStarsView(average: booking.driverRating.starsAverage, subject: "Motorista")
                                    Text("\(booking.origin) → \(booking.destination)")
                                    Text(booking.departureAt)
                                    Text(booking.price, format: .currency(code: "BRL"))
                                    if booking.canRate && !booking.hasRated {
                                        Text("Avalie o motorista anonimamente")
                                            .font(.caption)
                                        StarRatingPicker(selection: ratingBinding(for: booking.bookingId))
                                        Button("Enviar avaliação") {
                                            Task { await rate(bookingID: booking.bookingId) }
                                        }
                                        .disabled((selectedRatings[booking.bookingId] ?? 0) == 0 || isBusy)
                                    } else if booking.hasRated {
                                        Text("Você já avaliou esta viagem.").font(.caption).foregroundStyle(.secondary)
                                    }
                                    if booking.status == "confirmed" {
                                        Button("Desistir da reserva", role: .destructive) { bookingToCancel = booking }
                                    }
                                    EmergencyCallButton()
                                }
                                .padding(.vertical, 6)
                            }
                        }
                    } else {
                        Section("Publicar oferta") {
                            TextField("Ponto de partida", text: $origin)
                            DatePicker("Data e horário", selection: $departureAt, in: Date()..., displayedComponents: [.date, .hourAndMinute])
                            TextField("Valor por passageiro (R$)", text: $price).keyboardType(.decimalPad)
                            Stepper("Vagas: \(seats)", value: $seats, in: 1...4)
                            Button("Publicar no backend") { Task { await publishOffer() } }
                                .disabled(isBusy || origin.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty || (Double(price.replacingOccurrences(of: ",", with: ".")) ?? 0) <= 0)
                        }
                        Section("Passageiros das minhas viagens") {
                            if trips.isEmpty { Text("Reservas confirmadas aparecerão aqui.").foregroundStyle(.secondary) }
                            ForEach(trips) { trip in
                                VStack(alignment: .leading, spacing: 8) {
                                    Text("Passageiro: \(trip.passengerName)").font(.headline)
                                    RatingStarsView(average: trip.passengerRating.starsAverage, subject: "Passageiro")
                                    Text("\(trip.origin) → \(trip.destination)")
                                    Text(trip.departureAt)
                                    Text(trip.price, format: .currency(code: "BRL"))
                                    if trip.canRate && !trip.hasRated {
                                        Text("Avalie o passageiro anonimamente").font(.caption)
                                        StarRatingPicker(selection: ratingBinding(for: trip.bookingId))
                                        Button("Enviar avaliação") {
                                            Task { await rate(bookingID: trip.bookingId) }
                                        }
                                        .disabled((selectedRatings[trip.bookingId] ?? 0) == 0 || isBusy)
                                    } else if trip.hasRated {
                                        Text("Você já avaliou esta viagem.").font(.caption).foregroundStyle(.secondary)
                                    }
                                    EmergencyCallButton()
                                }
                                .padding(.vertical, 6)
                            }
                        }
                        Section("Minhas ofertas") {
                            ForEach(myOffers) { offer in
                                VStack(alignment: .leading, spacing: 8) {
                                    Text("\(offer.origin) → \(offer.destination)")
                                    Text(offer.departureAt)
                                    Text(offer.price, format: .currency(code: "BRL"))
                                    Text("\(offer.availableSeats) vagas restantes de \(offer.totalSeats)")
                                    if offer.status == "active" {
                                        Button("Cancelar oferta", role: .destructive) { offerToCancel = offer }
                                    }
                                    EmergencyCallButton()
                                }
                            }
                        }
                    }
                }

                if let infoMessage {
                    Section { Text(infoMessage).foregroundStyle(.green) }
                }
                if let errorMessage {
                    Section { Text(errorMessage).foregroundStyle(.red) }
                }
            }
            .navigationTitle("Viagens conectadas")
            .task(id: api.accessToken) {
                if api.isAuthenticated { await refresh() }
            }
            .confirmationDialog("Confirma o cancelamento da reserva?", isPresented: Binding(
                get: { bookingToCancel != nil }, set: { if !$0 { bookingToCancel = nil } }
            ), titleVisibility: .visible) {
                Button("Confirmar desistência", role: .destructive) {
                    guard let booking = bookingToCancel else { return }
                    bookingToCancel = nil
                    Task { await perform { try await api.cancelBooking(id: booking.bookingId) } }
                }
                Button("Manter reserva", role: .cancel) { bookingToCancel = nil }
            } message: {
                Text("A reserva será cancelada e a vaga ficará disponível novamente.")
            }
            .confirmationDialog("Confirma o cancelamento da oferta?", isPresented: Binding(
                get: { offerToCancel != nil }, set: { if !$0 { offerToCancel = nil } }
            ), titleVisibility: .visible) {
                Button("Confirmar cancelamento", role: .destructive) {
                    guard let offer = offerToCancel else { return }
                    offerToCancel = nil
                    Task { await perform { try await api.cancelOffer(id: offer.id) } }
                }
                Button("Manter oferta", role: .cancel) { offerToCancel = nil }
            } message: {
                Text("Os passageiros com reserva serão notificados pelo backend.")
            }
            .alert("Falha na conexão", isPresented: Binding(
                get: { errorMessage != nil }, set: { if !$0 { errorMessage = nil } }
            )) {
                Button("OK", role: .cancel) { errorMessage = nil }
            } message: {
                Text(errorMessage ?? "")
            }
            .alert("Aviso de viagem", isPresented: Binding(
                get: { activeNotification != nil }, set: { if !$0 { activeNotification = nil } }
            )) {
                Button("Entendi") {
                    guard let notification = activeNotification else { return }
                    activeNotification = nil
                    Task { await dismissNotification(notification) }
                }
            } message: {
                Text(activeNotification?.message ?? "")
            }
        }
    }

    private func ratingBinding(for bookingID: String) -> Binding<Int> {
        Binding(get: { selectedRatings[bookingID] ?? 0 }, set: { selectedRatings[bookingID] = $0 })
    }

    private func signIn() async {
        isBusy = true
        defer { isBusy = false }
        do {
            try await api.login(email: email, password: password)
            password = ""
            await refresh()
        } catch { errorMessage = error.localizedDescription }
    }

    private func refresh() async {
        guard let role = api.account?.role else { return }
        isBusy = true
        defer { isBusy = false }
        do {
            if role == "passenger" {
                async let available = api.fetchOffers()
                async let reservations = api.fetchBookings()
                async let alerts = api.fetchNotifications()
                offers = try await available
                bookings = try await reservations
                let fetchedAlerts = try await alerts
                let knownIDs = Set(notifications.map(\.id))
                notifications = fetchedAlerts
                if let newAlert = fetchedAlerts.first(where: { !knownIDs.contains($0.id) }) {
                    activeNotification = newAlert
                }
            } else {
                async let passengerTrips = api.fetchDriverTrips()
                async let postedOffers = api.fetchMyOffers()
                trips = try await passengerTrips
                myOffers = try await postedOffers
            }
            errorMessage = nil
        } catch { errorMessage = error.localizedDescription }
    }

    private func publishOffer() async {
        guard let amount = Double(price.replacingOccurrences(of: ",", with: ".")) else { return }
        await perform {
            try await api.createOffer(origin: origin.trimmingCharacters(in: .whitespacesAndNewlines), departureAt: departureAt, price: amount, seats: seats)
            origin = ""
            price = ""
        }
    }

    private func rate(bookingID: String) async {
        guard let stars = selectedRatings[bookingID], stars > 0 else { return }
        isBusy = true
        defer { isBusy = false }
        do {
            try await api.submitRating(bookingID: bookingID, stars: stars)
            selectedRatings[bookingID] = nil
            infoMessage = "Avaliação enviada anonimamente."
            await refresh()
        } catch { errorMessage = error.localizedDescription }
    }

    private func dismissNotification(_ notification: BackendNotification) async {
        do {
            try await api.markNotificationRead(id: notification.id)
            notifications.removeAll { $0.id == notification.id }
        } catch { errorMessage = error.localizedDescription }
    }

    private func perform(_ action: () async throws -> Void) async {
        isBusy = true
        defer { isBusy = false }
        do {
            try await action()
            infoMessage = "Ação concluída e sincronizada com o backend."
            await refresh()
        } catch { errorMessage = error.localizedDescription }
    }

    private func clearData() {
        offers = []
        bookings = []
        trips = []
        myOffers = []
        notifications = []
        activeNotification = nil
        selectedRatings = [:]
    }
}

// MARK: - App Entry Point
@main
struct RideOfferApp: App {
    @State private var viewModel = RideOfferListViewModel()
    @State private var backend = VaiProPoloBackend()

    var body: some Scene {
        WindowGroup {
            TabView {
                RideOffersListView(viewModel: viewModel)
                    .tabItem { Label("Passageiros", systemImage: "person.2") }

                DriverOfferFormView(viewModel: viewModel)
                    .tabItem { Label("Motorista", systemImage: "car") }

                DriverRegistrationView()
                    .tabItem { Label("Motorista", systemImage: "person.crop.circle.badge.plus") }

                PassengerRegistrationView()
                    .tabItem { Label("Passageiro", systemImage: "person.crop.circle") }

                BackendConnectionView(api: backend)
                    .tabItem { Label("Conectado", systemImage: "arrow.triangle.2.circlepath") }
            }
        }
    }
}

#Preview {
    RideOffersListView(viewModel: RideOfferListViewModel())
}
