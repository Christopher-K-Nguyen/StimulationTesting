function [File,quitProgram] = runVoltageTransient3(File)
warning('off','all');
% try
%% Constants
OWNER = 'ckn140030@utdallas.edu';
SAVE_PATH = 'Z:\2_ Projects and Data\SBIR Project\Data\Phase_II\';
BLACKROCK_TO_PLEXON_OMNETICS = [9,10,11,12,13,14,15,16,1,2,3,4,5,6,7,8];
STARTING_CHANNEL = 1;
NUM_OF_CHANNELS = 16;
CHARGE_CHECK = 1;
MAX_COUNT = 6;

%% Variables
scope = File.Oscilloscope.Object;
surfaceArea = File.SurfaceArea;
File.DateTimeCreated = getDateTime();
%     fig = figure(NUM_OF_CHANNELS+1);
%     tiledFig = tiledlayout(fig,2,8);
notebook = File.Notebook;
serial = File.SerialNumber;
serial_fix = strrep(serial,': ','');
foldername = strrep(serial_fix,'-','_');
fields = fieldnames(File);
stimType = File.Parameters.Type;
isPostAcute = strcmpi(stimType,'POST');
isTargetMax = strcmpi(stimType,'MAX');
postTest = [];
pattern = struct(...
    'A1',[],...
    'A2',[],...
    'W1',[],...
    'W2',[],...
    'Delay',[]);

isPulsing = false;
isLongPulsing = false;
if any(contains(fields,'Pulsing','IgnoreCase',true))
    isPulsing = true;
    pulsingFields = fieldnames(File.Pulsing);
    if any(contains(pulsingFields,'Long','IgnoreCase',true))
        isLongPulsing = true;
    end
end
if isLongPulsing
    [count,~] = size(File.Pulsing);
    idx = count - 1;
    pulseNum = File.PulsingData(count).PulseNumber;
    pulsingPeriod = File.Pulsing.PulsePeriod;
    chargePhase_arr = zeros(1,NUM_OF_CHANNELS);
    chargeInjection_arr = zeros(1,NUM_OF_CHANNELS);
    filename = sprintf('%s_%s_%03dx%g',notebook,foldername,idx,pulsingPeriod);
    filename = strrep(filename,'+0','');
    test = 'Pulsing';
    filepath = fullfile(SAVE_PATH,test,foldername);
else
    name = 'AnimalStudy';
    subjectSelect = File.Subject;
    week = File.Week;
    test = File.Test;
    foldername = sprintf('%s_W%02d',subjectSelect,week);
    filename = sprintf('%s_%s_%s',notebook,foldername,test);
    filepath = fullfile(SAVE_PATH,name,subjectSelect,foldername,test);
end
mkdir(filepath);
emailFiles = {};

%% Prepare stimulation for each channel
try
    stopStimAllChannel(1);
catch
end
% Stimulation parameters
phaseWidth1 = File.Parameters.PhaseWidth1;        % first phase pulse width
interphaseDelay = File.Parameters.InterphaseDelay;% interphase delay
phaseWidth2 = File.Parameters.PhaseWidth2;        % second phase pulse width
pulseWidth = phaseWidth1 + interphaseDelay + phaseWidth2;
stimRate = File.Parameters.StimulationRate;
stimPeriod = 1 / stimRate;
dutyCycle = phaseWidth1 * 1e-6 / stimPeriod;
dutyCycle_max = pulseWidth * 1e-6 / stimPeriod;
numOfPulses = File.Parameters.NumberOfPulses;     % number of pulses
if strcmpi(stimType,'POST')
    acceptDiff = 0.025; 	% water window
    isTargetMax = true;
    postTest = [isPostAcute dutyCycle];
    maxChannelTime = 420;
else
    acceptDiff = 0.020; 	% water window
    maxChannelTime = 480;
end
% if isTargetMax && isPulsing
%     amplitude1_arr = ones(1,NUM_OF_CHANNELS) * -20;
%     amplitude2_arr = -amplitude1_arr;
% else
%     amplitude1_data = File.Parameters.Amplitude1;
%     if isstruct(amplitude1_data)
%         amplitude1_arr = File.Parameters.Amplitude1(count).Amplitude;
%         amplitude2_arr = File.Parameters.Amplitude2(count).Amplitude;
%     else
amplitude1_arr = File.Parameters.Amplitude1;
amplitude2_arr = File.Parameters.Amplitude2;
%     end
% end

% Reference
if any(amplitude1_arr < 0)
    potentialLimit = File.ReferenceElectrode.LowerPotential;
else
    potentialLimit = File.ReferenceElectrode.UpperPotential;
end

%% Stimulation
startTime = tic;
% channelList = STARTING_CHANNEL:NUM_OF_CHANNELS;
for channelNum = STARTING_CHANNEL:NUM_OF_CHANNELS   % consecutively stimulate channels
    % Channel connection
    fprintf('Blackrock Channel %d...',channelNum);
    if contains(subjectSelect,'C')
        channelStim = channelNum;
    else
        channelStim = BLACKROCK_TO_PLEXON_OMNETICS(channelNum);
    end
    fprintf('Plexon Channel %d.\n',channelStim);
    File.Data(channelNum).Channel = channelNum;                  %#ok<*AGROW> % channel number
    File.Data(channelNum).SurfaceArea = surfaceArea;

    amplitude1 = amplitude1_arr(channelNum);
    setDefaultScopeView3(File,amplitude1);
    %     amplitude2 = amplitude2_arr(channelNum);
    amplitude1_sign = sign(amplitude1);
    amplitude1_mag = abs(amplitude1);
    currentStim = amplitude1_mag;
    currentChange_arr = currentStim;
    currentStim_arr = currentStim;
    potentialExcursionCheck_arr = [];
    isChargeMaxFound = false;
    isCurrentChecked = false;
    isWaveformStopped = false;
    isVoltageBad = false;
    captureNum = 0;
    beep;
    startChannelTime = tic;
    while ~isChargeMaxFound
        captureNum = captureNum + 1;
        % Stimulation change
        if currentStim >= 1000
            fprintf('Current compliance reached...');
            currentStim = 1000;
            isAtCurrentCompliance = true;
            fprintf('OK\n');
        else
            isChargeMaxFound = false;
            isAtCurrentCompliance = false;
            File.Data(channelNum).Capture(captureNum).Status.CurrentCompliance = false;
            File.Data(channelNum).Capture(captureNum).Status.Description = 'Good';
        end
        amplitude1_new = amplitude1_sign * currentStim; % new first phase amplitude
        amplitude2_new = -amplitude1_sign * currentStim; % new second phase amplitude

        % Set rectangular pulse parameters
        pattern.A1 = amplitude1_new;   	% first phase amplitude
        pattern.A2 = amplitude2_new; 	% second phase amplitude
        pattern.W1 = phaseWidth1;    	% first phase width
        pattern.W2 = phaseWidth2;     	% second phase width
        pattern.Delay = interphaseDelay;% interphase delay

        % Set stimulation parameters
        quitProgram = setStimParam(1,channelStim,pattern);
        if quitProgram
            return;
        end

        % Load parameters to channel
        quitProgram = loadChannel(1,channelStim);
        if quitProgram
            return;
        end

        % Set the monitor channel for all stimulators
        quitProgram = setMonitorChannel(1,channelStim);
        if quitProgram
            return;
        end
        isMonitor = false;
        while ~isMonitor
            [monitorChannel,~] = PS_GetMonitorChannel(1);
            isMonitor = monitorChannel == channelStim;
            if ~isMonitor
                fprintf('\tMonitor channel NOT set to Channel %d.\n',channelStim);
                fprintf('\t\t');
                setMonitorChannel(1,channelStim);
            end
        end

        % Set stimulation rate
        quitProgram = setStimRate(1,channelStim,stimRate);
        if quitProgram
            return;
        end

        % Set number of repetitions for all stimulators
        quitProgram = setNumOfPulses(1,channelStim,numOfPulses);
        if quitProgram
            return;
        end

        % Start Stimulation
        % Amplitude
        if amplitude1_new == 0
            fprintf('\tI = 0 uA\n');
        else
            fprintf('\tI = %g uA\n',amplitude1_new);
        end

        % Charge
        [chargePhase,chargeInjection] = getCharge(currentStim,phaseWidth1,surfaceArea);
        fprintf('\tQph = %g nC/ph\n',chargePhase);
        fprintf('\tQinj = %g mC/cm2\n',chargeInjection);
        File.Data(channelNum).Capture(captureNum).Amplitude = currentStim;
        File.Data(channelNum).Capture(captureNum).ChargePhase = chargePhase;       	    % charge per phase
        File.Data(channelNum).Capture(captureNum).ChargeInjection = chargeInjection;        % charge injection

        % Stimulation
        quitProgram = startStimChannel(1,channelStim);
        if quitProgram
            return;
        end
        isStimOn = false;
        stimCount = 0;
        while ~isStimOn
            [stimOn,~] = PS_ChannelStimStarted(1,channelStim);
            fprintf(scope,'ACQuire:STAte RUN');
%             fprintf(scope,'TRIGger FORCe');
            pause(1);
%             fprintf(scope,'ACQuire:STAte:STOPAfter SEQUENCE');
            fprintf(scope,'TRIGger:STATE?');
            triggerState = fscanf(scope);
            isTrigger = contains(triggerState,'TRIG','IgnoreCase',true);
            isReady = contains(triggerState,'REA','IgnoreCase',true);
            isStimOn = stimOn && isTrigger && ~isReady;
            if ~isStimOn
                stimCount = stimCount + 1;
                fprintf('\tStimulation NOT ON for Channel %d.\n',channelStim);
                if stimOn
                    fprintf('\t\t');
                    stopStimAllChannel(1);
                    pause(1);
                end
                fprintf('\t\t');
                setStimRate(1,channelStim,stimRate);
                fprintf('\t\t');
                startStimChannel(1,channelStim);
                pause(1);
                if stimCount == 1
                    fprintf(scope,'ACQuire:MODe SAMple');  % set waveform acquisition to samples
                    break;
                end
            else
                fprintf(scope,'ACQuire:MODe AVErage');  % set waveform acquisition to averages
            end
        end

        % Check Stimulation Rate
        isStimRate = false;
        stimRateCount = 0;
        while ~isStimRate
            stimRateCount = stimRateCount + 1;
            fprintf(scope,'TRIGger:MAIn:FREQuency?');
            triggerFreq_raw = str2num(fscanf(scope)); %#ok<*ST2NM>
            triggerFreq = round(triggerFreq_raw,2,'significant');
            [rate,~] = PS_GetRate(1,channelStim);
            isStimRate = rate == stimRate && triggerFreq == stimRate;
            if ~isStimRate
                fprintf('\tStimulation rate NOT set to %d pps.\n',stimRate);
                stopStimAllChannel(1);
                fprintf('\t\t');
                setStimRate(1,channelStim,stimRate);
                fprintf('\t\t');
                startStimChannel(1,channelStim);
                pause(1);
            end
            if stimRateCount == 1
                break;
            end
        end

        % Capture waveform
        scale = currentStim / 4 * 1e-3;
        scale_char = sprintf('%e',scale);
        scale_len = length(scale_char);
        e_idx = strfind(scale_char,'e');
        factor_char = scale_char(1:e_idx-1);
        factor_num = str2double(factor_char);
        scale_factor = (ceil(factor_num * 10) + 6.2) / 10;
        pow_char = scale_char(e_idx:scale_len);
        scale_new = sprintf('%.2f%s',scale_factor,pow_char);
        scale_use = sprintf('CH2:SCAle %s',scale_new);
        fprintf(scope,scale_use);
        setTriggerLevel(File,amplitude1_new);
        fprintf(scope,'ACQuire:STAte RUN');
        dateTime = getDateTime();
        File.Data(channelNum).Capture(captureNum).DateTime = dateTime;                   % date and time completed
        if ~isCurrentChecked
            [File,buttonHandle] = getWaveformData(File,channelNum,amplitude1_new,'BOTH',captureNum);
            isAtVoltageCompliance = File.Data(channelNum).Capture(captureNum).Status.VoltageCompliance;
            isVoltageSafe = File.Data(channelNum).Capture(captureNum).Status.VoltageSafety;
            isLimitReached = File.Data(channelNum).Capture(captureNum).Status.PotentialLimit;
            isQuit = File.Data(channelNum).Capture(captureNum).Status.Quit;
            isVoltageBad = isAtVoltageCompliance || ~isVoltageSafe;
            isVoltageStopped = isVoltageBad || any(isLimitReached);
            isChargeTooLow = chargePhase < CHARGE_CHECK && chargePhase > 0;
            isWaveformStopped = isVoltageStopped || isQuit || isAtCurrentCompliance;
            if ~ishandle(buttonHandle)  	% stop by button handle
                fprintf('OK\n');
                break;
            end
            isCurrentChecked = true;
        else
            if isTargetMax
                [File,buttonHandle] = getWaveformData(File,channelNum,amplitude1_new,'CH1',captureNum);
                isAtVoltageCompliance = File.Data(channelNum).Capture(captureNum).Status.VoltageCompliance;
                isVoltageSafe = File.Data(channelNum).Capture(captureNum).Status.VoltageSafety;
                isLimitReached = File.Data(channelNum).Capture(captureNum).Status.PotentialLimit;
                isQuit = File.Data(channelNum).Capture(captureNum).Status.Quit;
                isVoltageBad = isAtVoltageCompliance || ~isVoltageSafe;
                isVoltageStopped = isVoltageBad || any(isLimitReached);
                isChargeTooLow = chargePhase < CHARGE_CHECK && chargePhase > 0 && isVoltageStopped;
                isCurrentNeeded = isVoltageStopped || isChargeTooLow || isAtCurrentCompliance || any(isLimitReached);
                if isCurrentNeeded
                    fprintf('\n');
                    [File,buttonHandle] = getWaveformData(File,channelNum,amplitude1_new,'BOTH',captureNum);
                    isAtVoltageCompliance = File.Data(channelNum).Capture(captureNum).Status.VoltageCompliance;
                    isVoltageSafe = File.Data(channelNum).Capture(captureNum).Status.VoltageSafety;
                    isLimitReached = File.Data(channelNum).Capture(captureNum).Status.PotentialLimit;
                    isQuit = File.Data(channelNum).Capture(captureNum).Status.Quit;
                    isVoltageBad = isAtVoltageCompliance || ~isVoltageSafe;
                    isVoltageStopped = isVoltageBad || any(isLimitReached);
                    isChargeTooLow = chargePhase < CHARGE_CHECK && chargePhase > 0 && isVoltageStopped;
%                     isWaveformStopped = isVoltageStopped || isQuit || isChargeTooLow;
                    if isQuit  	% stop by button handle
                        fprintf('OK\n');
                        break;
                    end
                end
                if isQuit  	% stop by button handle
                    fprintf('OK\n');
                    break;
                end
            end
        end
        potentialExcursion_arr = File.Data(channelNum).Capture(captureNum).PotentialExcursion;
        potentialExcursion = potentialExcursion_arr(1);
        potentialExcursion_check = round(potentialExcursion,3);
        potentialExcursionCheck_arr_alloc = [potentialExcursionCheck_arr;potentialExcursion_check];
        potentialExcursionCheck_arr = potentialExcursionCheck_arr_alloc;
        [~,potentialExcursion_count] = mode(potentialExcursionCheck_arr);

        % Forcing channel stop
        if isVoltageBad || isChargeTooLow
            if isAtVoltageCompliance
                fprintf('Voltage compliance reached...');
            elseif ~isVoltageSafe
                fprintf('Voltage is unsafe...');
            elseif isChargeTooLow && isVoltageStopped
                fprintf('Charge is too low...');
            end
            isChargeMaxFound = true;
            fprintf('OK\n');
        else
            isChargeMaxFound = false;
        end
        switch isLimitReached
            case -1
                fprintf('Cathodic potential limit reached...');
                isChargeMaxFound = true;
                fprintf('OK\n');
            case 1
                fprintf('Anodic potential limit reached...');
                isChargeMaxFound = true;
                fprintf('OK\n');
        end
        elapsedChannelTime = toc(startChannelTime);
        if (elapsedChannelTime > maxChannelTime || potentialExcursion_count > MAX_COUNT)...
                && ~any(isLimitReached)...
                && ~isCurrentNeeded
            fprintf('Took too long...');
            captureNum = captureNum + 1;
            isChargeMaxFound = true;
            fprintf('OK\n');
            [File,~] = getWaveformData(File,channelNum,amplitude1_new,'BOTH',captureNum);
            File.Data(channelNum).Capture(captureNum).Status.Description = 'Took too long';
            File.Data(channelNum).Capture(captureNum).Status.TooLong = true;
        end

        % Adjust Current
        if isTargetMax && ~isVoltageStopped && ~isChargeMaxFound
            %             potentialExcursion_arr = File.Data(channelNum).Capture(captureNum).PotentialExcursion;
            %             potentialExcursion = potentialExcursion_arr(1);
            [currentStim_new,currentChange] = adjustCurrentStim2(...
                currentStim,...     % present current stimulation
                Inf,...
                0,...        % step size
                potentialLimit,...  % potential limit
                potentialExcursion,...    % max potential
                surfaceArea,...
                postTest);

            % Amplitude Monitor Array
            currentChange_arr_alloc = [currentChange_arr;currentChange];
            currentChange_arr = currentChange_arr_alloc;
            currentStim_arr_alloc = [currentStim_arr;currentStim];
            currentStim_arr = currentStim_arr_alloc;
            currentStim = checkCurrentChange(currentChange_arr,currentStim,currentStim_new);
            if currentStim == 1e3 && isAtCurrentCompliance
                fprintf('Current cannot be any larger...');
                isChargeMaxFound = true;
                File.Data(channelNum).Capture(captureNum).Status.Description = 'Current compliance reached';
                File.Data(channelNum).Capture(captureNum).Status.CurrentCompliance = true;
                fprintf('OK\n');
            end
%             if currentChange < 0
%                 setTriggerLevel(File,amplitude1_sign * currentStim);
%             end
        elseif ~isTargetMax || isVoltageBad
            isChargeMaxFound = true;
            %             break;
        end
    end
    %     fprintf('\n');
    % Stop stimulation channel
    %         if isWaveformStopped || isChargeMaxFound || isChargeTooLow
%     quitProgram = stopStimAllChannel(1);
                quitProgram = stopStimChannel(1,channelStim);
    if quitProgram
        break;
    end
    %         end

    %% Store data in structure
    
    % Store Values
    potentialExcursion_arr = File.Data(channelNum).Capture(captureNum).PotentialExcursion;
    potentialExcursion = potentialExcursion_arr(1);
    isPotentialExcursionGood = checkPotentialExcursion(File,potentialExcursion,acceptDiff);
%     if isVoltageBad || isChargeTooLow
%         potentialExcursion1 = 0;
%         potentialExcursion2 = 0;
%         accessVoltage1 = 0;
%         accessVoltage2 = 0;
%         drivingVoltage1 = 0;
%         drivingVoltage2 = 0;
%         potentialExcursion_arr = [potentialExcursion1;potentialExcursion2];
%         accessVoltage_arr = [accessVoltage1;accessVoltage2];
%         drivingVoltage_arr = [drivingVoltage1;drivingVoltage2];
%         File.Data(channelNum).Capture(captureNum).PotentialExcursion = potentialExcursion_arr;% max potential
%         File.Data(channelNum).Capture(captureNum).AccessVoltage = accessVoltage_arr;    % access voltage
%         File.Data(channelNum).Capture(captureNum).DrivingVoltage = drivingVoltage_arr;  % driving voltage
%     end
    if (~isTargetMax && ~isPotentialExcursionGood) || (isVoltageBad || isChargeTooLow)
        amplitude1_arr(channelNum) = 0;
        amplitude2_arr(channelNum) = 0;
    else
        amplitude1_arr(channelNum) = amplitude1_new;
        amplitude2_arr(channelNum) = amplitude2_new;
    end
    File.Data(channelNum).Capture(captureNum).Amplitude = currentStim;
    File.Data(channelNum).Capture(captureNum).ChargePhase = chargePhase;       	    % charge per phase
    File.Data(channelNum).Capture(captureNum).ChargeInjection = chargeInjection;        % charge injection
    if isLongPulsing
        chargeInjection_arr(channelNum) = chargeInjection;
        chargePhase_arr(channelNum) = chargePhase;
    end
    dateTime = getDateTime();
    File.Data(channelNum).Capture(captureNum).DateTime = dateTime;                   % date and time completed
    try
        buttonHandle.Visible = 'off';
        delete(buttonHandle);
    catch
        delete(buttonHandle);
    end
    File.Data(channelNum).Capture(captureNum).Figure = figure(channelNum);

    %% Saving files
    if isLongPulsing
        File.Parameters.Amplitude1(count).PulseNumber = pulseNum;
        File.Parameters.Amplitude1(count).Amplitude = amplitude1_arr;
        File.Parameters.Amplitude2(count).PulseNumber = pulseNum;
        File.Parameters.Amplitude2(count).Amplitude = amplitude2_arr;
        File.PulsingData(count).Data = Data;
    else
        File.Parameters.Amplitude1 = amplitude1_arr;
        File.Parameters.Amplitude2 = amplitude2_arr;
    end

    if isLongPulsing
        File.PulsingData(count).ChargePhase = chargePhase_arr;
        File.PulsingData(count).ChargeInjection = chargeInjection_arr;
        if istargetMax
            if count == 1
                File.PulsingData(count).Percentage = zeros(1,NUM_OF_CHANNELS) + 100;
            else
                chargeInjection_arr_first = File.PulsingData(1).ChargeInjection;
                percentage_arr = chargeInjection_arr ./ chargeInjection_arr_first * 100;
                percentage_round_arr = round(percentage_arr);
                File.PulsingData(count).Percentage = percentage_round_arr;
            end
        end
    end
    dateTimeModified = getDateTime();
    File.DateTimeModified = dateTimeModified;
    %     fprintf('Saving Channel %d...\n',channelNum);
    [file_mat,file_xlsx] = saveData3(...
        File,...
        filename,...
        filepath,...
        channelNum);
    %     fprintf('\n');
    if ishandle(figure(channelNum))
        file_tif = saveChannelFig(...
            File,...
            channelNum,...
            isVoltageBad,...
            filename,...
            filepath);
    else
        file_tif = [];
    end
    emailFiles_alloc = [emailFiles,file_tif];
    emailFiles = emailFiles_alloc;
    %     fprintf('\n');

    %% Change channel
    [~,stopStim,endTime] = changeChannel(...
        1,...
        channelNum,NUM_OF_CHANNELS,...
        startTime,startChannelTime,...
        1);
    fprintf('\n');
    if stopStim == true  % stop stimulation after current channel
        break;          % quit after this channel
    end
end

%% Email
emailFiles_alloc = [emailFiles,file_xlsx,file_mat];
emailFiles = emailFiles_alloc;
if isLongPulsing
    pulseNum_new = addCommas(pulseNum);
    subject = sprintf('%s Pulses %s W%02d',pulseNum_new);
    subject = strrep(subject,'+','');
else
    if isPostAcute
        %         stimRate_use = addCommas(stimRate);
        %         subject = sprintf('%s %s pps',filename,stimRate_use);
        subject = sprintf('%s pps',filename);
    else
        subject = '';
    end
end
emailAddress = File.Email;
sendEmail(filename,emailAddress,endTime,emailFiles,subject);
if ~strcmpi(emailAddress,OWNER)
    sendEmail(filename,OWNER,endTime,emailFiles,subject);
end
fprintf('\n');

% catch err
%     File.Error.Status = err;
%     report = getReport(err);
%     File.Error.Report = report;
%     display(report);
%     quitProgram = true;
% end

end