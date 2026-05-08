function [File,isQuit] = runCapture2_Tek(File)
close all;
warning('off','all');
try
    %% Constants
    N_TO_MICRO = 1e6;
    MICRO_TO_N = 1e-6;
    MILLI_TO_N = 1e-3;

    %% Variables
    numOfDevices = length(File.Oscilloscope);
    subject = File.Subject;
    folderpath = File.Path;
    dateTimeCreated = getDateTime();
    File.DateTimeCreated = dateTimeCreated;
    attachments_tif = {};
    attachments = {};

    % file_mat = [subject '.mat'];
    % filepath_mat = fullfile(folderpath,file_mat);
    % delete(filepath_mat);
    % 
    % file_xlsx = [subject '.xlsx'];
    % filepath_xlsx = fullfile(folderpath,file_xlsx);
    % delete(filepath_xlsx);

    %% Capture
    startTime = tic;
    isQuit = false;
    isAgain = false;
    while ~isQuit
        % Index
        [File,isQuit] = getIndex2_Tek(File,isAgain);
        if isQuit
            break;
        end

        % Info
        [File,isQuit] = getIndexInfo_Tek(File);
        if isQuit
            break;
        end

        confirmCapture = false;
        while ~confirmCapture
            startCaptureTime = tic;
            capture_arr = [File.Data.Index];
            captureNum = length(capture_arr);
            for deviceNum = 1:numOfDevices
                setStatus_Tek(File,deviceNum,'open');
                channelSelect_cell = File.Oscilloscope(deviceNum).Channels;
                channelName_cell = File.Oscilloscope(deviceNum).ChannelNames;
                numOfChannels = File.Oscilloscope(deviceNum).NumberOfChannels;
                if deviceNum == 1
                    time = getTime_Tek(File,deviceNum) * N_TO_MICRO;
                    File.Data(captureNum).Time = time;
                end
                for channel_idx = 1:numOfChannels
                    channel = channelSelect_cell{channel_idx};
                    channelName = channelName_cell{channel_idx};
                    [data,~] = getWaveform_Tek(File,deviceNum,channel);
                    if numOfDevices == 2 && deviceNum == 1
                        switch channel_idx
                            case 1
                                channelField = 'Voltage';
                            case 2
                                channelField = 'Current';
                        end
                    else
                        channelField = channelSelect_cell{channel_idx};
                    end
                    File.Data(captureNum).(channelField) = data;
                    if contains2(channelName,'Current')
                        current_A = data * MICRO_TO_N;
                        surfaceArea = File.Data(captureNum).SurfaceArea;
                        currentDensity = getCurrentDensity(current_A,surfaceArea) * MILLI_TO_N;
                        File.Data(captureNum).CurrentDensity = currentDensity;
                    end
                end
            end
            [endCaptureTime,unit] = getEndTime(startCaptureTime);
            fprintf('Time Elapse: %.2f %s\n',endCaptureTime,unit);
            [File,fig] = getPlot_new_Tek(File);
            dateTime = getDateTime();
            File.Data(captureNum).DateTime = dateTime;
            File.DateTimeModified = dateTime;
            % File.Data(captureNum).Figure = fig;
            beep;
            [confirmCapture,isSave,isAgain] = getSave_Tek();
            if ~confirmCapture && ~isSave
                continue;
            elseif confirmCapture && ~isSave
                break;
            end
        end

        if isQuit
            break;
        end

        if isSave
            [filepath_mat,filepath_csv,filepath_xlsx] = saveData_Tek(File);
            attachments_alloc = [attachments,filepath_csv];
            attachments = attachments_alloc;
            filepath_tif = saveFig_Tek(File,fig);
            attachments_tif_alloc = [attachments_tif,filepath_tif];
            attachments_tif = attachments_tif_alloc;
            fprintf('\n');
        end
    end
    if isQuit
        fprintf('\nQuitting...\n\n');
        return;
    end
    
    if ~isempty(attachments)
        [endTime,unit] = getEndTime(startTime);
        attachments = [attachments_tif,filepath_mat,filepath_xlsx];
        filename_zip = [subject '.zip'];       	% filename for .zip file
        filepath_zip = fullfile(folderpath,filename_zip);  % save path for .zip file
        try
            zip(filepath_zip,attachments);
        catch
            attachments = {filepath_mat,filepath_xlsx};
            zip(filepath_zip,attachments);
        end
    
        emailAddress = File.Email;
        emailSubject = [subject ' ' dateTimeCreated];
        sendEmail2( ...
            emailAddress, ...
            emailSubject, ...
            filepath_zip, ...
            endTime,unit);
        try
            delete(filepath_zip);
        catch
        end
    end

catch err
    beep;pause(0.5);beep;pause(0.5);beep;
    File.Error.Status = err;
    report = getReport(err);
    File.Error.Report = report;
    display(report);
    isQuit = true;
end

end