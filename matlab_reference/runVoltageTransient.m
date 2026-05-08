function [File,isQuit] = runVoltageTransient(File)
close all;
close(findall(0,'type','figure'));
try
    delete(findall(groot));
catch
end
clc;
warning('off','all');
isQuit = false;
stopStimulation();
try
    %% Variables
    fields = fieldnames(File);
    notebook = File.Notebook;
    subject = File.Subject;
    emailAddress = File.User.Email;
    phone = File.User.Phone;
    carrier = File.User.Carrier;
    saveFolder = File.Path;
    if ~isempty(subject)
        expName = [notebook '_' subject];
    else
        expName = notebook;
    end
    foldername = expName;
    fileName = expName;
    file_zip = '';

    % Device
    deviceType = File.Parameters.Device;

    % Environment
    environment = File.Parameters.Environment;
    isAnimal = contains2(environment,'Animal');

    % Channels
    plexonChannel_arr = File.Parameters.Channels.Plexon;
    testChannel_arr = File.Parameters.Channels.Test;

    % Surface Area
    surfaceArea_arr = File.Parameters.SurfaceArea;
    numOfSurfaceArea = length(surfaceArea_arr);

    % Electrode
    activeElectrode = File.Parameters.WorkingElectrode.Type;
    refElectrode = File.Parameters.ReferenceElectrode.Type;
    returnElectrode = File.Parameters.CounterElectrode.Type;

    % Configuration
    configID = File.Parameters.Configuration.ID;
    isMP = contains2(configID,'MP');
    isPBP = contains2(configID,'PBP');
    isBP = contains2(configID,'BP') && ~isPBP;
    isPTP = contains2(configID,'PTP');
    isPartial = isPBP || isPTP;
    isCG = contains2(configID,'CG');
    channelGroup_mat = File.Test.Groups;
    [numOfGroups,numOfChannelsUsed] = size(channelGroup_mat);

    % Experiment
    expType = File.Test.Experiment;
    isTriphasic = contains2(expType,'TV');

    % Test Type
    testType = File.Test.ID;
    isTargetMax = contains2(testType,'max');

    % Oscillscopes
    numOfDevices = length(File.Oscilloscope);
    isUSB_tf = zeros(numOfDevices,1);
    for deviceNum = 1:numOfDevices
        resourceName = File.Oscilloscope(deviceNum).Resource;
        isUSB_tf(deviceNum) = contains2(resourceName,'usb');
    end
    % isOnlyUSB = all(isUSB_tf);
    fieldsList = vertcat(File.Oscilloscope(:).Fields);
    activeChannel_tf = containsi(fieldsList,{'act','work','pot'});
    diffChannel_tf = containsi(fieldsList,{'diff'});
    voltageChannel_tf = containsi(fieldsList,{'volt'});
    if any(activeChannel_tf)
        specialChannel_idx = find(activeChannel_tf);
    elseif any(diffChannel_tf)
        specialChannel_idx = find(diffChannel_tf);
    elseif any(voltageChannel_tf)
        specialChannel_idx = find(voltageChannel_tf);
    else
        specialChannel_idx = 1;
    end
    specialChannel = fieldsList(specialChannel_idx);
    isVoltageSpecial = contains2(specialChannel,'volt');
    hasReturnChannel = contains2(fieldsList,{'ret','count'});
    hasAltActive = isVoltageSpecial && hasReturnChannel;

    % Fields
    Capture = File.Data(1).Capture;
    field_cell = fieldnames(Capture);
    timeField_idx = find(containsi(field_cell,'Time'));
    currentDensity_idx = find(containsi(field_cell,'CurrentDensity'));
    dataFields_cell = field_cell(timeField_idx+1:currentDensity_idx-1);
    activeField_tf = containsi(dataFields_cell,{'act','work','pot'});
    hasActiveField = any(activeField_tf);
    diffField_tf = containsi(dataFields_cell,{'diff'});
    hasDiffField = any(diffField_tf);
    voltageField_tf = containsi(dataFields_cell,{'volt'});
    hasVoltageField = any(voltageField_tf);
    if hasAltActive || hasActiveField || hasDiffField
        electrode_use = 'reference';
    else
        electrode_use = 'counter';
    end

    % Pulsing
    isPulsing = contains2(fields,'Pulsing');
    if isPulsing
        pulsingFields = fieldnames(File.Pulsing);
        isLongPulsing = contains2(pulsingFields,'Long');
    else
        isLongPulsing = false;
    end
    if isLongPulsing
        [count,~] = size(File.Pulsing);
        idx = count - 1;
        pulseNum = File.PulsingData(count).PulseNumber;
        pulsingPeriod = File.Pulsing.PulsePeriod;
        chargePhase_arr = zeros(1,numOfGroups);
        chargeInjection_arr = zeros(1,numOfGroups);
        fileName = sprintf('%s_%s_%03dx%g',notebook,foldername,idx,pulsingPeriod);
        fileName = strrep(fileName,'+0','');
        test = 'Pulsing';
        folderPath = fullfile(saveFolder,test,foldername);
        mkdir(folderPath);
    else
        filePath_old = fullfile(saveFolder,foldername);
        folderPath = mkdir2(filePath_old);
        [~,foldername,~] = fileparts(folderPath);
        fileName = foldername;
    end
    File.Name = fileName;
    File.Path = folderPath;
    emailSubject = sprintf('MATLAB VT: %s',fileName);
    file_tif_cell = cell(numOfGroups,1);

    % Progress Bar
    prog = 0;
    progMsg = sprintf('Voltage Transients: %d / %d (%.2f%%)',prog,numOfGroups,prog);
    progBar = waitbar(prog,progMsg,'Name','Voltage Transient Progress');

    % Status
    Status = struct( ...
        'Description','', ...
        'Good',true, ...
        'Normal',true, ...
        'PotentialLimit',0, ...getWa
        'VoltageSafety',true, ...
        'VoltageCompliance',false, ...
        'MaxCurrent',false, ...
        'TooLong',false, ...
        'Quit',false);

    isOn = File.Stimulator.Status;
    if isOn
        %% Prepare stimulation for each channel
        % Stimulation parameters
        phaseWidth1 = File.Parameters.PhaseWidth1;        % first phase width
        phaseWidth2 = File.Parameters.PhaseWidth2;        % second phase width
        interphaseDelay = File.Parameters.InterphaseDelay;% interphase delay
        if isTriphasic
            phaseWidth3 = File.Parameters.PhaseWidth3;        % thurd phase width
        end
        amplitude_ratio = abs(File.Parameters.AmplitudeRatio);
        [factor_max,leadingPhase] = max(amplitude_ratio);
        stimRate = File.Parameters.StimulationRate;
        polarity = File.Parameters.Polarity;
        isSymmetric = File.Parameters.Symmetry;
        if isTriphasic
            pattern = struct( ...
                'A1',0, ...
                'A2',0, ...
                'A3',0, ...
                'W1',phaseWidth1, ...
                'W2',phaseWidth2, ...
                'W3',phaseWidth3, ...
                'Delay',interphaseDelay);
        else
            pattern = struct( ...
                'A1',0, ...
                'A2',0, ...
                'W1',phaseWidth1, ...
                'W2',phaseWidth2, ...
                'Delay',interphaseDelay);
        end
        numOfPulses = File.Parameters.NumberOfPulses;     % number of pulses
        if isAnimal
            maxChannelTime = 240;
        else
            % if isOnlyUSB
            %     maxChannelTime = 900;
            % else
            %     maxChannelTime = 1500;
            % end
            maxChannelTime = Inf;
        end
        % if isTargetMax && isPulsing
        %     amplitude1_arr = File.Parameters.Amplitude1
        %     amplitude2_arr = File.Parameters.Amplitude2;
        % else
        %     amplitude1_data = File.Parameters.Amplitude1;
        %     if isstruct(amplitude1_data)
        %         amplitude1_arr = File.Parameters.Amplitude1(count).Amplitude;
        %         amplitude2_arr = File.Parameters.Amplitude2(count).Amplitude;
        %     else
        amplitude1_arr_old = File.Parameters.Amplitude1;
        amplitude2_arr_old = File.Parameters.Amplitude2;
        if isTriphasic
            amplitude3_arr_old = File.Parameters.Amplitude3;
        end
        %     end

        if isMP
            % Set stimulation rate
            isQuit = setAllStimRate(stimRate);
            if isQuit
                error('setAllStimRate');
            end

            % Set number of repetitions for all stimulators
            isQuit = setAllNumOfPulses(numOfPulses);
            if isQuit
                error('setAllNumOfPulses');
            end
            fprintf('\n');
        end

        %% Stimulation
        startTime = tic;
        for groupNum = 1:numOfGroups % consecutively stimulate channels
            clc;
            % Channel connection
            channelNum = channelGroup_mat(groupNum,1);
            % plexonChannel = plexonChannel_arr(channelNum);
            activeChannelName = sprintf('Channel %02d',channelNum);
            % groupNum = testChannel_arr == channelNum;
            fprintf('%s Channel %d...',deviceType,channelNum);
            channelStim = plexonChannel_arr(channelNum);
            fprintf('Plexon Channel %d\n',channelStim);
            File.Data(groupNum).ActiveChannel = channelNum;
            if numOfSurfaceArea > 1
                surfaceArea = surfaceArea_arr(groupNum);
            else
                surfaceArea = surfaceArea_arr;
            end
            File.Data(groupNum).SurfaceArea = surfaceArea;
            targetCharge = File.Test.ChargePhase;
            targetAmplitude = roundStim(targetCharge / phaseWidth1 * 1e3);
            stepSize = File.Test.StepSize;
            hasStepSize = any(stepSize);
            amplitude1_arr = amplitude1_arr_old;
            amplitude2_arr = amplitude2_arr_old;
            File.Parameters.Amplitude1 = amplitude1_arr;
            File.Parameters.Amplitude2 = amplitude2_arr;
            if isTriphasic
                amplitude3_arr = amplitude3_arr_old;
                File.Parameters.Amplitude3 = amplitude3_arr;
            end

            % Set the monitor channel
            if isMP
                channelTitle = activeChannelName;
                isQuit = setMonitorChannel(File,channelNum);
                if isQuit
                    break;
                end
                channelReturn_arr = [];
            else
                channelReturn_arr = channelGroup_mat(groupNum,2:numOfChannelsUsed);
                plexonReturnChannel_arr = plexonChannel_arr(channelReturn_arr);
                if ~isCG
                    fprintf('\tReturn channels...');
                    if isBP || isPBP
                        channelReturn_use = sprintf('%d',channelReturn_arr);
                        plexonReturnChannel_use = num2str(plexonReturnChannel_arr);
                    else
                        channelReturn_char = sprintf('%d ',channelReturn_arr);
                        len = length(channelReturn_char);
                        channelReturn_char(len) = [];
                        channelReturn_cell = strsplit(channelReturn_char);
                        channelReturn_use = strjoin(channelReturn_cell,', ');
                        plexonReturnChannel_char = char2(plexonReturnChannel_arr);
                        plexonReturnChannel_use = strjoin(plexonReturnChannel_char,', ');
                    end
                    fprintf('%s Channel %s...',deviceType,channelReturn_use);
                    fprintf('Plexon Channel %s\n',plexonReturnChannel_use);
                end
                File.Data(groupNum).ReturnChannel = channelReturn_arr;
                if isPartial
                    returnElectrode_use = erase(returnElectrode,...
                        {activeElectrode,' + '});
                    channelTitle = [activeChannelName ...
                        ' versus Channel ' channelReturn_use ...
                        ', ' returnElectrode_use];
                elseif isCG
                    channelTitle = [activeChannelName ' versus Common'];
                else
                    channelTitle = [activeChannelName ...
                        ' versus Channel ' channelReturn_use];
                end
            end
            progFract = (groupNum - 1) / numOfGroups;
            progPercent = progFract * 100;
            progMsg = sprintf('Voltage Transients: %d / %d (%.2f%%)', ...
                groupNum-1,numOfGroups,progPercent);
            channelTitle_msg = [channelTitle '...'];
            progMsg_cell = {progMsg,channelTitle_msg};
            waitbar(progFract,progBar,progMsg_cell);

            % Initialize View
            File = setOscilloscopeStatus(File,'open');
            File = setOscillocopeView(File,0);
            amplitude1 = amplitude1_arr(groupNum);
            amplitude2 = amplitude2_arr(groupNum);
            amplitude1_mag = abs(amplitude1);
            amplitude2_mag = abs(amplitude2);
            amplitude1_factor = amplitude_ratio(1);
            amplitude2_factor = amplitude_ratio(2);
            currentStim1 = roundStim(amplitude1_mag);
            currentStim2 = roundStim(amplitude2_mag);
            if isTriphasic
                amplitude3 = amplitude3_arr(groupNum);
                amplitude3_mag = abs(amplitude3);
                amplitude3_factor = amplitude_ratio(3);
                currentStim3 = roundStim(amplitude3_mag);
            end
            switch leadingPhase
                case 1
                    currentStim = currentStim1;
                    currentChange = amplitude1;
                case 2
                    currentStim = currentStim2;
                    currentChange = amplitude2;
                case 3
                    currentStim = currentStim3;
                    currentChange = amplitude3;
            end
            isTargetFound = false;
            isVoltageBad = false;
            captureNum = 0;
            
            beep;
            startChannelTime = tic;
            while ~isTargetFound
                startPrepTime = tic;
                captureNum = captureNum + 1;
                File.Data(groupNum).Capture(captureNum).Index = captureNum;
                File.Data(groupNum).Capture(captureNum).Status = Status;

                % Use amplitude ratios to maintain proportionality
                currentStim1 = roundStim(currentStim * amplitude1_factor / factor_max);
                currentStim2 = roundStim(currentStim * amplitude2_factor / factor_max);
                if isTriphasic
                    currentStim3 = roundStim(currentStim * amplitude3_factor / factor_max);
                else
                    currentStim3 = 0;
                end

                % Stimulation change
                isAtMaxCurrent = currentStim >= 1e3;
                if isAtMaxCurrent
                    fprintf('Max current reached...');
                    % Scale all proportionally to fit within 1 mA max
                    currentStim = 1e3;
                    currentStim1 = roundStim(currentStim * amplitude1_factor / factor_max);
                    currentStim2 = roundStim(currentStim * amplitude2_factor / factor_max);
                    if isTriphasic
                        currentStim3 = roundStim(currentStim * amplitude3_factor / factor_max);
                    end
                    File.Data(groupNum).Capture(captureNum). ...
                        Status.MaxCurrent = isAtMaxCurrent;
                    fprintf('OK\n');
                else
                    isTargetFound = false;
                    File.Data(groupNum).Capture(captureNum). ...
                        Status.MaxCurrent = false;
                    File.Data(groupNum).Capture(captureNum). ...
                        Status.Description = 'Good';
                end

                % Apply polarity to get final amplitudes
                amplitude1_new = roundStim(polarity * currentStim1);
                amplitude2_new = roundStim(-polarity * currentStim2);
                charge1 = amplitude1_new * phaseWidth1;
                charge2 = amplitude2_new * phaseWidth2;
                if isTriphasic                    
                    amplitude3_new = roundStim(polarity * currentStim3);
                    charge3 = amplitude3_new * phaseWidth3;
                else
                    amplitude3_new = 0;
                    charge3 = 0;
                end
                isAtFixedTarget = currentStim == targetAmplitude;
                isBalanced = abs(charge1 + charge2 + charge3) < 1e-3;
                if ~isBalanced
                    % Recalculate currents from charge_base
                    currentStim1 = roundStim(currentStim * amplitude1_factor / factor_max);
                    amplitude1_new = roundStim(polarity * currentStim1);
                    if isTriphasic
                        currentStim2 = roundStim(currentStim * amplitude2_factor / factor_max);
                        amplitude2_new = roundStim(-polarity * currentStim2);
                        charge3 = -(charge1 + charge2);
                        amplitude3_new = roundStim(charge3 / phaseWidth3); % uA
                        currentStim3 = abs(amplitude3_new);
                    else
                        charge2 = -charge1;
                        amplitude2_new = roundStim(charge2 / phaseWidth2); % uA
                        currentStim2 = abs(amplitude3_new);
                    end
                end
                switch leadingPhase
                    case 1
                        amplitude_new = amplitude1_new;
                        phaseWidth = phaseWidth1;
                    case 2
                        amplitude_new = amplitude2_new;
                        phaseWidth = phaseWidth2;
                    case 3
                        amplitude_new = amplitude3_new;
                        phaseWidth = phaseWidth3;
                end
                File.Data(groupNum).Capture(captureNum).Amplitude = amplitude_new;
                File.Data(groupNum).Capture(captureNum).PhaseWidth = phaseWidth;
                File.Data(groupNum).Capture(captureNum).CurrentChange = currentChange;
                File.Parameters.Amplitude1(groupNum) = amplitude1_new;
                File.Parameters.Amplitude2(groupNum) = amplitude2_new;
                if isTriphasic
                    File.Parameters.Amplitude3(groupNum) = amplitude3_new;
                end
                if isBP || isPBP
                    channelReturn = channelReturn_arr;
                    return_idx = find(testChannel_arr == channelReturn,1);
                    amplitude = File.Parameters.Mapping.Bipolar.ActiveRow;
                    if amplitude == 0
                        File.Parameters.Mapping.Bipolar. ...
                            ActiveRow(groupNum,return_idx) = amplitude_new;
                    else
                        File.Parameters.Mapping.Bipolar. ...
                            ActiveCol(return_idx,groupNum) = amplitude_new;
                    end
                end
                if captureNum > 1
                    amplitude_arr = [File.Data(groupNum).Capture(:).Amplitude];
                    % currentStimList = abs(amplitude_arr);
                    isAmplitudeRepeat_tf = ismember(amplitude_arr,amplitude_new);
                    isAmplitudeRepeat_idx = find(isAmplitudeRepeat_tf);
                    isAmplitudeRepeat = length(isAmplitudeRepeat_idx) > 3;
                    % isAmplitudeRepeat = any(ismember(amplitude1,amplitude_arr));
                else
                    isAmplitudeRepeat = false;
                end
                charge1 = amplitude1_new * phaseWidth1 / 1e3;
                charge2 = amplitude2_new * phaseWidth2 / 1e3;
                if isTriphasic
                    charge3 = amplitude3_new * phaseWidth3 / 1e3;
                else
                    charge3 = 0;
                end
                isBalanced = abs(charge1 + charge2 + charge3) < 1e-6;
                if ~isBalanced
                    fprintf('Charge 1 = %.2f nC/ph\n',charge1);
                    fprintf('\tAmplitude 1: %.2f uA\n',amplitude1_new);
                    fprintf('\tPhase Width 1 = %.2f us\n',phaseWidth1);
                    fprintf('Charge 2 = %.2f nC/ph\n',charge2);
                    fprintf('\tAmplitude 2: %.2f uA\n',amplitude2_new);
                    fprintf('\tPhase Width 2 = %.2f us\n',phaseWidth2);
                    if isTriphasic
                        fprintf('Charge 3 = %.2f nC/ph\n',charge3);
                        fprintf('\tAmplitude 3: %.2f uA\n',amplitude3_new);
                        fprintf('\tPhase Width 3 = %.2f us\n',phaseWidth3);
                    end
                    error('Not charge-balanced!');
                end

                % Set rectangular pulse parameters
                pattern.A1 = amplitude1_new;   	% first phase amplitude
                pattern.A2 = amplitude2_new; 	% second phase amplitude
                if isTriphasic
                    pattern.A3 = amplitude3_new; 	% third phase amplitude
                end

                % Set stimulation parameters
                isQuit = setPattern(File,channelNum,pattern,channelReturn_arr);
                if isQuit
                    break;
                end

                % Electrode
                File.Data(groupNum).Active = activeElectrode;
                File.Data(groupNum).Return = returnElectrode;
                File.Data(groupNum).Reference = refElectrode;
                if isVoltageSpecial
                    electrode = refElectrode;
                else
                    electrode = returnElectrode;
                end
                if isAnimal
                    refElectrode_use = sprintf('%s in animal',electrode);
                else
                    refElectrode_use = sprintf('%s in electrolyte',electrode);
                end
                File.Data(groupNum).Reference = refElectrode_use;

                % Load parameters to channel
%                 isQuit = loadAllChannels(1);
                isQuit = loadPattern(File,channelReturn_arr);
                if isQuit
                    break;
                end

                % Start Stimulation
                fprintf('Stimulation intensity:\n');
                % Amplitude
                if isTriphasic
                    fprintf('\tIstim(1) = %.2f uA\n',amplitude1_new);
                    fprintf('\tIstim(2) = %.2f uA\n',amplitude2_new);
                    fprintf('\tIstim(3) = %.2f uA\n',amplitude3_new);
                else
                    fprintf('\tIstim(1) = %.2f uA\n',amplitude1_new);
                    if ~isSymmetric
                        fprintf('\tIstim(2) = %.2f uA\n',amplitude2_new);
                    end
                end

                % Charge
                [chargePhase,chargeInjection] = getCharge( ...
                    currentStim,phaseWidth,surfaceArea);
                fprintf('\tQph = %g nC/ph\n',chargePhase);
                fprintf('\tQinj = %g mC/cm2\n',chargeInjection);
                % charge/phase
                File.Data(groupNum).Capture(captureNum). ...
                    ChargePhase = chargePhase;
                % charge injection
                File.Data(groupNum).Capture(captureNum). ...
                    ChargeInjection = chargeInjection;
                
                % Stimulation
                isQuit = startStimulation(File,channelReturn_arr);
                % for deviceNum = 1:numOfDevices
                %     oscilloscope = File.Oscilloscope(deviceNum).Object;
                %     fprintf(oscilloscope,'AUTOSet EXECute');
                % end
                % if ~isempty(channelReturn_arr) && ~isCG
                %     fprintf('\tNot stimulating...');
                %     checkStimTime = tic;
                %     for channelReturnNum = channelReturn_arr
                %         plexonReturn = plexonChannel_arr(channelReturnNum);
                %         [isStimOn,~] = PS_ChannelStimStarted(1,plexonReturn);
                %         if isStimOn
                %             fprintf('(%d)...',channelReturnNum)
                %         else
                %             fprintf('%d...',channelReturnNum)
                %         end
                %     end
                %     [endCheckTime,unit] = getEndTime(checkStimTime);
                %     fprintf('OK \t\t(%.2f %s)\n',endCheckTime,unit);
                % else
                %     if ~isCG
                %         numOfTestChannels = length(testChannel_arr);
                %         for testChannel_idx = 1:numOfTestChannels
                %             testChannelNum = testChannel_arr(testChannel_idx);
                %             [isStimOn,~] = PS_ChannelStimStarted(1,testChannelNum);
                %             if ~isStimOn
                %                 error('Return channel is stimulating!');
                %             end
                %         end
                %     end
                % end
                if isQuit
                    return;
                end

                % Set View
%                 fprintf('\t');
                amplitude_fix = max([currentStim1 currentStim2 currentStim3]);
                setOscilloscopeCurrentScale(File,amplitude_fix);
                [endPrepTime,unit] = getEndTime(startPrepTime);
                fprintf('Preparation Time: %.2f %s\n',endPrepTime,unit);
                fprintf('\n');

                % Capture waveform
                fprintf('Capture Number: %d\n',captureNum);
                %                 pause(3);
                dateTime = getDateTime();
                File.Data(groupNum).Capture(captureNum). ...
                    DateTime = dateTime;    % date and time completed
                [File,buttonHandle] = getWaveformData(File);
                [isLimitReached_check,~,isExceeded] = checkPotentialExcursion(File,electrode_use);
                elapsedChannelTime = toc(startChannelTime);
                if elapsedChannelTime < maxChannelTime
                    stopStimulation();
                end
                isAtVoltageCompliance = File.Data(groupNum). ...
                    Capture(captureNum).Status.VoltageCompliance;
                isVoltageSafe = File.Data(groupNum).Capture(captureNum). ...
                    Status.VoltageSafety;
                isLimitReached = File.Data(groupNum).Capture(captureNum). ...
                    Status.PotentialLimit;
                isQuit = File.Data(groupNum).Capture(captureNum).Status.Quit;
                isVoltageBad = isAtVoltageCompliance || ~isVoltageSafe;
                isVoltageStopped = isVoltageBad || isLimitReached;
                % if isQuit  	% stop by button handle
                %     fprintf('OK\n');
                %     break;
                % end
                if isLimitReached
                    if isLimitReached < 0
                            fprintf('Cathodic potential limit reached...');
                    elseif isLimitReached > 0
                            fprintf('Anodic potential limit reached...');                            
                    end
                    isTargetFound = true;
                    fprintf('OK\n');
                end
                if isVoltageBad
                    if isAtVoltageCompliance
                        fprintf('Voltage compliance reached...');
                    elseif ~isVoltageSafe
                        fprintf('Voltage is unsafe...');
                    end
                    isTargetFound = true;
                    fprintf('OK\n');
                end

                % Forcing channel stop
                if (elapsedChannelTime > maxChannelTime || isAmplitudeRepeat)...
                        && ~isLimitReached
                    [endChannelTime,unit] = getEndTime(startChannelTime);
                    if isAmplitudeRepeat
                        fprintf('Not enough precision \t\t(%.2f %s)...',endChannelTime,unit);
                        discription = 'Not enough precision';
                        File.Data(groupNum).Capture(captureNum). ...
                        Status.Precise = false;
                    else
                        fprintf('Took too long \t\t(%.2f %s)...',endChannelTime,unit);
                        discription = 'Took to long';
                        File.Data(groupNum).Capture(captureNum). ...
                        Status.TooLong = true;
                    end
                    % captureNum = captureNum + 1;
                    % File.Data(channel_idx).Capture(captureNum). ...
                    %     Amplitude = amplitude1_new;
                    % File.Data(channel_idx).Capture(captureNum). ...
                    %     ChargePhase = chargePhase;       	    % charge per phase
                    % File.Data(channel_idx).Capture(captureNum). ...
                    %     ChargeInjection = chargeInjection;        % charge injection
                    File.Data(groupNum).Capture(captureNum). ...
                        Status.Description = discription;
                    File.Data(groupNum).Capture(captureNum).Status.Good = false;
                    fprintf('OK\n');
                    fprintf('Capture Number: %d\n',captureNum);
                    [File,buttonHandle,isQuit] = getWaveformData(File);
                    stopStimulation();
                    isTargetFound = true;
                end
                if isQuit  	% stop by button handle
                    fprintf('OK\n');
                    break;
                end
                if isVoltageBad
                    if isAtVoltageCompliance
                        fprintf('Voltage compliance reached...');
                    elseif ~isVoltageSafe
                        fprintf('Voltage is unsafe...');
                    end
                    isTargetFound = true;
                    fprintf('OK\n');
                end
                if any(isLimitReached)
                    if isLimitReached < -1
                        fprintf('Cathodic potential limit reached...');
                    else
                        fprintf('Anodic potential limit reached...');
                    end
                    isTargetFound = true;
                    fprintf('OK\n');
                end
                if isAtFixedTarget
                    fprintf('Fixed target reached...');
                    isTargetFound = true;
                    fprintf('OK\n');
                end

                % Adjust Current
                if ~isVoltageStopped && ~isTargetFound
                    if (isTargetMax || (~isAtFixedTarget && hasStepSize))
                        [File,currentStim,currentChange] = changeCurrent_Fit(File,electrode_use);

                        % Calculate new currents preserving proportionality
                        if currentStim > 1e3
                            currentStim = 1e3;
                        end
                        currentStim1 = roundStim(currentStim / factor_max * amplitude1_factor);
                        currentStim2 = roundStim(currentStim / factor_max * amplitude2_factor);
                        if isTriphasic
                            currentStim3 = roundStim(currentStim1 / factor_max * amplitude3_factor);
                        else
                            currentStim3 = 0;
                        end

                        % Check max current constraint
                        currentStim_arr = [currentStim1 currentStim2 currentStim3];
                        if any(currentStim_arr >= 1e3)  && isAtMaxCurrent && ~isExceeded
                            fprintf('Current cannot be any larger...');
                            isTargetFound = true;
                            File.Data(groupNum).Capture(captureNum). ...
                                Status.Description = 'Max current reached';
                            File.Data(groupNum).Capture(captureNum). ...
                                Status.MaxCurrent = true;
                            fprintf('OK\n');
                        end
                    elseif ~hasStepSize || isVoltageBad
                        isTargetFound = true;
                    end
                else
                    isTargetFound = true;
                end
                if isTargetFound
                    break;
                end
            end
            %     fprintf('\n');
            if isQuit
                fprintf('OK\n\n');
                break;
            end
            %         end

            %% Store data in structure
            % Store Values
            % Capture
            File.Data(groupNum).Capture(captureNum). ...
                Amplitude = amplitude_new;
            File.Data(groupNum).Capture(captureNum). ...
                ChargePhase = chargePhase;       	    % charge per phase
            File.Data(groupNum).Capture(captureNum). ...
            ChargeInjection = chargeInjection;
            % Group Number
            File.Data(groupNum).Amplitude = amplitude_new;
            File.Data(groupNum).ChargePhase = chargePhase;       	    % charge per phase
            File.Data(groupNum).ChargeInjection = chargeInjection;
            if isMP
                channelMapping = File.Parameters.Mapping.Channel;
                channelMapping_idx = find(channelMapping == channelNum);
                File.Parameters.Mapping.Amplitude(channelMapping_idx) = amplitude1_new;
                File.Parameters.Mapping.ChargePhase(channelMapping_idx) = chargePhase;
                File.Parameters.Mapping.ChargeInjection(channelMapping_idx) = chargeInjection;
                if ~isTargetMax && ~isLimitReached_check ...
                        || isAtVoltageCompliance || isVoltageBad
                    amplitude1_arr(groupNum) = NaN;
                    amplitude2_arr(groupNum) = NaN;
                else
                    amplitude1_arr(groupNum) = amplitude1_new;
                    amplitude2_arr(groupNum) = amplitude2_new;
                end
            end
            if isLongPulsing
                chargeInjection_arr(groupNum) = chargeInjection;
                chargePhase_arr(groupNum) = chargePhase;
            end
            dateTime = getDateTime();
            File.Data(groupNum).Capture(captureNum).DateTime = dateTime; % date and time completed
            File.Data(groupNum).DateTime = dateTime;
            if isgraphics(buttonHandle)
                if ishandle(buttonHandle)
                    try
                        buttonHandle.Visible = 'off';
                        delete(buttonHandle);
                    catch
                        delete(buttonHandle);
                    end
                    % File.Data(groupNum).Figure = figure(groupNum);
                end
            end
            elapsedChannelTime = toc(startChannelTime);
            File.Data(groupNum).SecondsElapase = elapsedChannelTime;
            status = File.Data(groupNum).Capture(captureNum).Status.Description;
            File.Data(groupNum).Status = status;

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
                        File.PulsingData(count).Percentage = zeros(1,numOfGroups) + 100;
                    else
                        chargeInjection_arr_first = File.PulsingData(1).ChargeInjection;
                        percentage_arr = chargeInjection_arr ./ chargeInjection_arr_first * 100;
                        percentage_round_arr = round(percentage_arr);
                        File.PulsingData(count).Percentage = percentage_round_arr;
                    end
                end
            end
            if ishandle(figure(groupNum))
                file_tif = saveChannelFigure(File);
            else
                file_tif = [];
            end
            dateTimeModified = getDateTime();
            File.DateTimeModified = dateTimeModified;
            %     fprintf('Saving Channel %d...\n',channelNum);
            [file_mat,file_xlsx] = saveVoltageTransientData(File);
            
            % Message
            channelID = File.Data(groupNum).ID;
            msg = sprintf('%s (%d / %d)',channelID,groupNum,numOfGroups);
            sendPictureText(File,emailSubject,msg,file_tif);
            %     fprintf('\n');
            
            file_tif_cell{groupNum} = file_tif;
            %     fprintf('\n');
            %% Change channel
            progFract = groupNum / numOfGroups;
            progPercent = progFract * 100;
            progMsg = sprintf('Voltage Transients: %d / %d (%.2f%%)', ...
                groupNum,numOfGroups,progPercent);
            channelTitle_msg = [channelTitle '...' status];
            progMsg_cell = {progMsg,channelTitle_msg};
            waitbar(progFract,progBar,progMsg_cell);
            [endChangeTime,unit] = getEndTime(startChannelTime);
            File.Data(groupNum).TimeElapse = sprintf('%.2f %s', ...
                endChangeTime,unit);
            [isAgain,isStop] = changingChannel( ...
                File, ...
                startTime,startChannelTime);
            if isAgain
                groupNum = groupNum - 1; %#ok<*FXSET
                File.Data(groupNum).Capture = [];
            end
            if isStop  % stop stimulation after current channel
                break;          % quit after this channel
            else
                fprintf('\n');
            end
        end
        progFract = groupNum / numOfGroups;
        progPercent = progFract * 100;
        progMsg = sprintf('Voltage Transients: %d / %d (%.2f%%)', ...
            groupNum,numOfGroups,progPercent);
        channelTitle_msg = strrep(['"' fileName '"' '...Done!'],'_','\_');
        progMsg_cell = {progMsg,channelTitle_msg};
        waitbar(progFract,progBar,progMsg_cell);
        File = setOscilloscopeStatus(File,'close');
        if isQuit
            return;
        end

        %% Email
        % if isLongPulsing
        %     pulseNum_new = addCommas(pulseNum);
        %     weekNum = File.Week;
        %     subjectName = sprintf('%s Pulses %s W%02d',pulseNum_new,weekNum);
        %     subjectName = strrep(subjectName,'+','');
        % else
        % 
        % end
        % Email
        if ~isempty(emailAddress) || (~isempty(phone) && ~isempty(carrier))
            dirFile = dir(folderPath);
            dirFileByte_arr = [dirFile(:).bytes];
            dirFileByte_max = sum(dirFileByte_arr);
            dirFileMByte_max = dirFileByte_max / 2^20;
            isFileSmall = false;
            if dirFileMByte_max < 25
                isFileSmall = true;
                files_cell = {file_xlsx;file_mat;file_tif_cell};
            else
                dirFile_xlsx = dir(file_xlsx);
                dirFile_mat = dir(file_mat);
                dirFileByte = dirFile_xlsx.bytes + dirFile_mat.bytes;
                dirFileMByte = dirFileByte / 2^20;
                if dirFileMByte < 25
                    isFileSmall = true;
                    files_cell = {file_xlsx;file_mat};
                end
            end
            if isFileSmall
                zipFilename = [fileName '.zip'];       	% fileName for .zip file
                file_zip = fullfile(folderPath,zipFilename);  % save path for .zip file
                fprintf('Compressing files...');
                startCompressTime = tic;
                try                    
                    zip(file_zip,files_cell);
                    attachements = file_zip;
                    [endCompressTime,unit] = getEndTime(startCompressTime);
                    fprintf('%s \t\t(%.2f %s)\n',zipFilename,endCompressTime,unit);
                catch
                    try
                        files_cell = [file_xlsx;file_tif_cell];
                        zip(file_zip,files_cell);
                        attachements = file_zip;
                        [endCompressTime,unit] = getEndTime(startCompressTime);
                        fprintf('%s (%.2f %s)\n',zipFilename,endCompressTime,unit);
                    catch
                        attachements = [];
                        [endCompressTime,unit] = getEndTime(startCompressTime);
                        fprintf('FAILED \t\t(%.2f %s)\n',endCompressTime,unit);
                    end
                end
            else
                file_zip = [];
                attachements = [file_mat;file_tif_cell];
            end

            [endTime,unit] = getEndTime(startTime);
            sendMessage(File,emailSubject,attachements,endTime,unit);
        end
        if ~isempty(file_zip)
            startDeleteTime = tic;
            fprintf('Deleting %s...',zipFilename);
            delete(file_zip);
            [endDeleteTime,unit] = getEndTime(startDeleteTime);
            fprintf('OK \t\t(%.2f %s)\n',endDeleteTime,unit);
        end
        %         fprintf('\n');
    end

catch err
    fprintf('\n');
    stopStimulation();
    beep;pause(0.5);
    beep;pause(0.5);
    beep;
    File = setOscilloscopeStatus(File,'close');
    fields_check = fieldnames(File);
    if contains2(fields_check,'Error')
        idx = 2;
    else
        idx = 1;
    end
    File.Error(idx).Status = err;
    report = getReport(err);
    report_short = getReport(err,'extended','hyperlinks','off');
    File.Error(idx).Report = report;
    display(report);
    try
        channel_arr = [File.Data(:).ActiveChannel];
        groupNum = length(channel_arr);
        channelName = File.Data(groupNum).Name;
        fprintf('%s (%d / %d)\n',channelName,groupNum,numOfGroups);
    catch
    end

    varName = getVarName(File);
    newVarName = ['FailedFile_' fileName];
    setNewVarName = sprintf('%s = %s;',newVarName,varName);
    eval(setNewVarName);
    fileName_mat = [fileName '.mat'];       	% fileName for .mat file
    tempName_mat = [fileName '_temp.mat'];
    filePath_mat = fullfile(folderPath,fileName_mat);  % save path for .mat file
    tempPath_mat = fullfile(folderPath,tempName_mat);
    attachements = filePath_mat;

    zipFilename = [fileName '.zip'];       	% fileName for .zip file
    file_zip = fullfile(folderPath,zipFilename);  % save path for .zip file
    fprintf('Compressing files...');
    startCompressTime = tic;
    try
        zip(file_zip,attachements);
        attachements = file_zip;
        [endCompressTime,unit] = getEndTime(startCompressTime);
        fprintf('%s \t\t(%.2f %s)\n',zipFilename,endCompressTime,unit);
        try
            save(filePath_mat,newVarName);             	% save .mat file
        catch
            save(tempPath_mat,newVarName);
            delete(filePath_mat);
            movefile(tempPath_mat,filePath_mat,'f');
        end
    catch
        attachements = [];
    end
    emailSubject = sprintf('MATLAB VT Failed: %s',fileName);
    [endTime,unit] = getEndTime(startTime);
    sendError(File,emailSubject,report_short,attachements,endTime,unit);
    
    startDeleteTime = tic;
    fprintf('Deleting %s...',zipFilename);
    delete(file_zip);
    [endDeleteTime,unit] = getEndTime(startDeleteTime);
    fprintf('OK \t\t(%.2f %s)\n',endDeleteTime,unit);

    isQuit = true;
end
fprintf('\n');

end