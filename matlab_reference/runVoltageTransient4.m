function [File,quitProgram] = runVoltageTransient4(File)
close all;
warning('off','all');
try
    %% Constants
    OWNER = 'ckn140030@utdallas.edu';
    SAVE_PATH = 'Z:\2_ Projects and Data\SBIR Project\Data\Phase_II\';

    %% Variables
    File = setScopeStatus(File,'open');
    subjectSelect = File.Subject;
    numOfChannels = File.Parameters.NumberOfChannels;
%     reftShift = File.ReferenceElectrode.Shift;
    isAnimal = contains2(subjectSelect,'A') && ~contains2(subjectSelect,'PA04');
    channelMapping = File.Parameters.Mapping.Channel;
    surfaceArea_arr = File.Parameters.SurfaceArea;
    plexonChannel_arr = File.Parameters.Channels.Plexon;
    File.DateTimeCreated = getDateTime();
    %     fig = figure(NUM_OF_CHANNELS+1);
    %     tiledFig = tiledlayout(fig,2,8);
    notebook = File.Notebook;
    serial = File.SerialNumber;
    serial_fix = strrep(serial,': ','');
    foldername = strrep(serial_fix,'-','_');
    fields = fieldnames(File);
    stimType = File.Parameters.Type;
    isMultiTest = contains2(stimType,'MULTI');
    isRateTest = contains2(stimType,'RATE');
    isTargetMax = contains2(stimType,'MAX') || isRateTest;
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
        chargePhase_arr = zeros(1,numOfChannels);
        chargeInjection_arr = zeros(1,numOfChannels);
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
        expName = sprintf('%s_%s',notebook,foldername);
        filename = sprintf('%s_%s_%s',notebook,foldername,test);
        filepath = fullfile(SAVE_PATH,name,subjectSelect,foldername,test);
    end
    mkdir(filepath);
    attachments = {};

    %% Initialize connection with stimulators
    % Initialize stimulator
    [File,quitProgram] = initAllStim2(File);
    if quitProgram
        fprintf('OK.\n\n');
        return;
    end

    % Find stimulators
    [numOfStim,quitProgram] = getNumOfStim();
    if quitProgram
        return;
    end
    if numOfStim == 1
        % Find channels available per stimulators (should be 16, value not used)
        [~,quitProgram] = getChannelsFromStim(numOfStim);
        if quitProgram
            return;
        end
    else
        fprintf('NO STIMULATORS CONNECTED!\n\n');
        return;
    end
%     fprintf('\n');
    if ~quitProgram
        %% Prepare stimulation for each channel
        % Stimulation parameters
        phaseWidth1 = File.Parameters.PhaseWidth1;        % first phase pulse width
        interphaseDelay = File.Parameters.InterphaseDelay;% interphase delay
        phaseWidth2 = File.Parameters.PhaseWidth2;        % second phase pulse width
        stimRate = File.Parameters.StimulationRate;
        pattern = struct( ...
            'A1',0, ...
            'A2',0, ...
            'W1',phaseWidth1, ...
            'W2',phaseWidth2, ...
            'Delay',interphaseDelay);
        numOfPulses = File.Parameters.NumberOfPulses;     % number of pulses
        if isRateTest
            maxChannelTime = 240;
%             chargeCheck = 0.1;
        else
            maxChannelTime = 300;
%             chargeCheck = 1;
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
        amplitude1_arr = File.Parameters.Amplitude1;
        amplitude2_arr = File.Parameters.Amplitude2;
        %     end

        %% Stimulation
        % Set stimulation rate
        quitProgram = setAllStimRate(stimRate);
        if quitProgram
            return;
        end

        % Set number of repetitions for all stimulators
        quitProgram = setAllNumOfPulses(numOfPulses);
        if quitProgram
            return;
        end
        fprintf('\n');

        startTime = tic;
        % channelList = 1:NUM_OF_CHANNELS;
        for channelNum = 1:numOfChannels   % consecutively stimulate channels
            % Channel connection
            fprintf('Blackrock Channel %d...',channelNum);
            if isAnimal
                channelStim = plexonChannel_arr(channelNum);
            else
                channelStim = channelNum;
            end
            fprintf('Plexon Channel %d\n',channelStim);
            File.Data(channelNum).Channel = channelNum;                  %#ok<*AGROW> % channel number
            surfaceArea = surfaceArea_arr(channelNum);
            File.Data(channelNum).SurfaceArea = surfaceArea;
            
            % Set the monitor channel
            quitProgram = setMonitorChannel2(File,channelStim);
            if quitProgram
                break;
            end

            % Initialize View
            amplitude1 = amplitude1_arr(channelNum);
            setDefaultScopeView3(File,amplitude1);
            amplitude1_sign = sign(amplitude1);
            amplitude1_mag = abs(amplitude1);
            currentStim = amplitude1_mag;
            isChargeMaxFound = false;
            isBad = false;
            isVoltageStopped = false;
            isVoltageBad = false;
            captureNum = 0;
            currentChange = amplitude1;
            beep;
            startChannelTime = tic;
            while ~isChargeMaxFound
                startLoadTime = tic;
                captureNum = captureNum + 1;
                File.Data(channelNum).Capture(captureNum).Index = captureNum;
                
                % Stimulation change
                if currentStim >= 1000
                    fprintf('Max current reached...');
                    currentStim = 1000;
                    isAtMaxCurrent = true;
                    File.Data(channelNum).Capture(captureNum).Status.MaxCurrent = isAtMaxCurrent;
                    fprintf('OK\n');
                else
                    isChargeMaxFound = false;
                    isAtMaxCurrent = false;
                    File.Data(channelNum).Capture(captureNum).Status.MaxCurrent = false;
                    File.Data(channelNum).Capture(captureNum).Status.Description = 'Good';
                end
                amplitude1_new = amplitude1_sign * currentStim; % new first phase amplitude
                File.Data(channelNum).Capture(captureNum).Amplitude = amplitude1_new;
                File.Data(channelNum).Capture(captureNum).CurrentChange = currentChange;
                amplitudeList = [File.Data(channelNum).Capture.Amplitude];
                idx_shift = 5;
                if captureNum > idx_shift
                    check_idx = captureNum - idx_shift;
                    amplitudeList_check = amplitudeList(check_idx:captureNum);
                    [~,modeCount] = mode(amplitudeList_check);
                    isAmplitudeRepeat = any(modeCount > 1);
                else
                    isAmplitudeRepeat = false;
                end
                
                amplitude2_new = -amplitude1_sign * currentStim * (phaseWidth1 / phaseWidth2); % charge-balanced second phase amplitude (A1*W1 = A2*W2)

                % Set rectangular pulse parameters
                pattern.A1 = amplitude1_new;   	% first phase amplitude
                pattern.A2 = amplitude2_new; 	% second phase amplitude

                % Set stimulation parameters
                if isMultiTest
                    [channelReturn,dist] = getNeighbor(File,channelStim);
                    quitProgram = setMultipolarStim2(File,channelStim,channelReturn,pattern);
                else
                    quitProgram = setMonopolarStim2(File,channelStim,pattern);
                    refElectrode_arr = File.ReferenceElectrode.Type;
                    isMulitRef = iscell(refElectrode_arr);
                    if isMulitRef
                        refElectrode = refElectrode_arr{1};
                    else
                        refElectrode = refElectrode_arr;
                    end
                    if isAnimal || isMulitRef
                        if isMulitRef
                            refElectrode_new = refElectrode_arr{2};
                        else
                            refElectrode_new = refElectrode_arr;
                        end
                        refElectrode_use = sprintf('%s in vivo (%s)',refElectrode,refElectrode_new);
                    else
                        refElectrode_use = sprintf('%s in vitro',refElectrode);
                    end
                    File.Data(channelNum).Return = refElectrode_use;
                end
                if quitProgram
                    break;
                end

                % Load parameters to channel
                quitProgram = loadAllChannels(1);
                if quitProgram
                    break;
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
%                 isChargeTooLow = chargePhase < chargeCheck && chargePhase > 0;
                fprintf('\tQph = %g nC/ph\n',chargePhase);
                fprintf('\tQinj = %g mC/cm2\n',chargeInjection);
                File.Data(channelNum).Capture(captureNum).ChargePhase = chargePhase;       	    % charge per phase
                File.Data(channelNum).Capture(captureNum).ChargeInjection = chargeInjection;        % charge injection

                % Stimulation
                quitProgram = startStimChannel2(1,channelStim);
                if quitProgram
                    break;
                end

                % Set View
                fprintf('\t');
                setCurrentScale(File,amplitude1_new);
                fprintf('\t');
                setTriggerLevel2(File);
                [endLoadTime,unit] = getEndTime(startLoadTime);
                fprintf('Load Time: %.2f %s\n',endLoadTime,unit);
                fprintf('\n');
                
                % Capture waveform
                fprintf('Capture Number: %d\n',captureNum);
%                 pause(3);
                dateTime = getDateTime();
                File.Data(channelNum).Capture(captureNum).DateTime = dateTime;                   % date and time completed
                [File,buttonHandle] = getWaveformData2(File);
                stopStimAllChannels(1);
                isAtVoltageCompliance = File.Data(channelNum).Capture(captureNum).Status.VoltageCompliance;
                isVoltageSafe = File.Data(channelNum).Capture(captureNum).Status.VoltageSafety;
                isLimitReached = File.Data(channelNum).Capture(captureNum).Status.PotentialLimit;
                isQuit = File.Data(channelNum).Capture(captureNum).Status.Quit;
                isVoltageBad = isAtVoltageCompliance || ~isVoltageSafe;
                isVoltageStopped = isVoltageBad || any(isLimitReached);
                if isQuit  	% stop by button handle
                    fprintf('OK\n');
                    break;
                end

                % Forcing channel stop
                if isVoltageBad
                    if isAtVoltageCompliance
                        fprintf('Voltage compliance reached...');
                    elseif ~isVoltageSafe
                        fprintf('Voltage is unsafe...');
%                     elseif isChargeTooLow
%                         fprintf('Charge is too low...');
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
                if isAmplitudeRepeat && ~isChargeMaxFound
                    fprintf('Not enough amplitude precision...');
                    File.Data(channelNum).Capture(captureNum).Status.Good = false;
                    isChargeMaxFound = true;
                    fprintf('OK\n');
                end
                elapsedChannelTime = toc(startChannelTime);
                if (elapsedChannelTime > maxChannelTime)...
                        && ~any(isLimitReached)
                    fprintf('Took too long...');
                    captureNum = captureNum + 1;
                    File.Data(channelNum).Capture(captureNum).Amplitude = amplitude1_new;
                    File.Data(channelNum).Capture(captureNum).ChargePhase = chargePhase;       	    % charge per phase
                    File.Data(channelNum).Capture(captureNum).ChargeInjection = chargeInjection;        % charge injection
                    File.Data(channelNum).Capture(captureNum).Status.Description = 'Took too long';
                    File.Data(channelNum).Capture(captureNum).Status.TooLong = true;
                    fprintf('OK\n');
                    fprintf('Capture Number: %d\n',captureNum);
                    [File,buttonHandle] = getWaveformData2(File);
                    isChargeMaxFound = true;
                end

                % Adjust Current
                if isTargetMax && ~isVoltageStopped && ~isChargeMaxFound
                    [currentStim,currentChange] = changeCurrentStim(File);
                    if currentStim >= 1e3 && isAtMaxCurrent
                        fprintf('Current cannot be any larger...');
                        isChargeMaxFound = true;
                        File.Data(channelNum).Capture(captureNum).Status.Description = 'Max current reached';
                        File.Data(channelNum).Capture(captureNum).Status.MaxCurrent = true;
                        fprintf('OK\n');
                    end
                elseif ~isTargetMax || isVoltageBad
                    isChargeMaxFound = true;
                end
            end
            %     fprintf('\n');
            % Stop stimulation channel
            quitProgram = stopStimAllChannels(1);
            if quitProgram
                fprintf('OK\n\n');
                break;
            end
            %         end

            %% Store data in structure
            % Store Values
            File.Data(channelNum).Capture(captureNum).Amplitude = amplitude1_new;
            File.Data(channelNum).Capture(captureNum).ChargePhase = chargePhase;       	    % charge per phase
            File.Data(channelNum).Capture(captureNum).ChargeInjection = chargeInjection;
            [isLimitReached_check,~] = checkPotentialExcursion(File);
            if (~isTargetMax && ~any(isLimitReached_check)) ...
                || isAtVoltageCompliance || isVoltageBad
                amplitude1_arr(channelNum) = 0;
                amplitude2_arr(channelNum) = 0;
            else
                amplitude1_arr(channelNum) = amplitude1_new;
                amplitude2_arr(channelNum) = amplitude2_new;
            end
            channel_idx = find(channelMapping == channelNum);
            amplitude_new = amplitude1_arr(channelNum);
            [chargePhase_new,chargeInjection_new] = getCharge( ...
                amplitude_new,phaseWidth1,surfaceArea);
            File.Parameters.Mapping.Amplitude(channel_idx) = amplitude_new;
            File.Parameters.Mapping.ChargePhase(channel_idx) = chargePhase_new;
            File.Parameters.Mapping.ChargeInjection(channel_idx) = chargeInjection_new;
                 % charge injection
            if isLongPulsing
                chargeInjection_arr(channelNum) = chargeInjection;
                chargePhase_arr(channelNum) = chargePhase;
            end
            dateTime = getDateTime();
            File.Data(channelNum).Capture(captureNum).DateTime = dateTime;                   % date and time completed
            if isgraphics(buttonHandle)
                if ishandle(buttonHandle)
                    try
                        buttonHandle.Visible = 'off';
                        delete(buttonHandle);
                    catch
                        delete(buttonHandle);
                    end
                    File.Data(channelNum).Figure = figure(channelNum);
                end
            end
            status = File.Data(channelNum).Capture(captureNum).Status.Description;
            File.Data(channelNum).Status = status;

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
                        File.PulsingData(count).Percentage = zeros(1,numOfChannels) + 100;
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
            File = setScopeStatus(File,'close');
%             File.Data(channelNum).Capture(captureNum+1:1000) = [];
            [file_mat,file_xlsx] = saveData3( ...
                File, ...
                filename, ...
                filepath);
            %     fprintf('\n');
            if ishandle(figure(channelNum))
                file_tif = saveChannelFig2( ...
                    File, ...
                    filename, ...
                    filepath);
            else
                file_tif = [];
            end
            attachments_alloc = [attachments,file_tif];
            attachments = attachments_alloc;
            %     fprintf('\n');

            %% Change channel
            [endChannelTime,unit] = getEndTime(startChannelTime);
            File.Data(channelNum).TimeElapse = sprintf('%.2f %s', ...
                endChannelTime,unit);
            [~,isStop,endTime_cell] = changeChannel2( ...
                channelNum,numOfChannels, ...
                startTime,startChannelTime);
            if isStop == true  % stop stimulation after current channel
                break;          % quit after this channel
            else
                fprintf('\n');
            end
        end
        if quitProgram
            return;
        end

        %% Email
%         attachments_alloc = [attachments,file_xlsx,file_mat];
        dataFiles = [file_xlsx,file_mat,attachments];
        zipFilename = append(filename,'.zip');       	% filename for .zip file
        file_zip = fullfile(filepath,zipFilename);  % save path for .zip file
        zip(file_zip,dataFiles)
%         attachments = [attachments_alloc,file_zip];
        if isLongPulsing
            pulseNum_new = addCommas(pulseNum);
            weekNum = File.Week;
            subject = sprintf('%s Pulses %s W%02d',pulseNum_new,weekNum);
            subject = strrep(subject,'+','');
        else
            if isRateTest
                stimRate_use = addCommas(stimRate);
                subject = sprintf('%s %s pps',stimType,stimRate_use);
            else
                subject = stimType;
            end
        end
        % Email
        emailAddress = File.Email;
        emailSubject = sprintf('MATLAB Stimulation: %s %s',expName,subject);
        sendEmail2(emailAddress,emailSubject,file_zip,endTime_cell);
        if ~contains2(emailAddress,OWNER)
            sendEmail2(OWNER,emailSubject,file_zip,endTime_cell);
        end
        try
            delete(file_zip);
        catch
        end
%         fprintf('\n');
    end
    %% Close all stimulators
    closeAllStim2();
    beep;pause(0.5);beep;

catch err
    closeAllStim2();
    beep;pause(0.1);beep;pause(0.1);beep;pause(0.1);beep;pause(0.1);beep;
    File.Error.Status = err;
    report = getReport(err);
    File.Error.Report = report;
    display(report);
    quitProgram = true;
end
fprintf('\n');

end