function [filename_mat_save] = savePulsing(...
    filename,...
    filepath,...
    File)
%% Constants
N_TO_MICRO = 1e6;

%% Variables
fields = fieldnames(File);
hasArea = any(contains(fields,'SurfaceArea','IgnoreCase',true));

%% Function
% Starting filename
filename_char = filename;
% filenameAll_char = append(filename_char,'_ALL'); 	% filename with all channels
filenameAll_char = filename_char;

% Making .mat file
fprintf('Saving MATLAB file...');
filename_mat = append(filenameAll_char,'.mat');       	% filename for .mat file
filename_mat_save = fullfile(filepath,filename_mat);  % save path for .mat file
save(filename_mat_save,'File');             	% save .mat file
fprintf('%s\n',filename_mat);         	% .mat file saved

% % Making spreadsheet files
% fprintf('Saving Excel file...');
% filename_xlsx = append(filenameAll_char,'.xlsx');           % filename for .xlsx file
% filename_xlsx_save = fullfile(filepath,filename_xlsx); 	% save path for .xlsx file
% channel_idx_last = find(channelSelect == channelNum_stop);
% channelList = {};
% chargePhaseList = [];
% chargeInjList = [];
% maxPotentialList = [];
% drivingVoltageList = [];
% for channel_idx = 1:channel_idx_last    % loop through channel data
%     channelNum = channelSelect(channel_idx);
%     % Capture data of channel
%     time_data = data(channelNum).Time * N_TO_MICRO;              % time data array
%     voltage_data = data(channelNum).VoltageTransient;            % voltage data array
%     current_data = data(channelNum).CurrentStimulation;          % current data array
%     chargePhase_data = data(channelNum).ChargePhase;      	% charge per phase value
%     if hasArea
%         geomSurfaceArea_data = data(channelNum).GeometricSurfaceArea;% geometric surface area
%     end
%     chargeInj_data = data(channelNum).ChargeInjection;           % charge injection value
%     maxPotential_data = data(channelNum).MaxPotentialExcursion;   % maximum cathodal potential value
%     voltageDrive_data = data(channelNum).DrivingVoltage;           % driving voltage value
%     dateTime_data = data(channelNum).DateTime;                   % date and time completed
%     status_data = data(channelNum).Status;
% 
%     % Arrays for single values
%     blank_len = length(time_data);          % length of data
%     blank_array = strings(blank_len,1);	% array of blank space
%     chargePerPh_strings = blank_array;
%     chargePerPh_strings(1) = string(chargePhase_data);     	% add charge per phase to array
%     if hasArea
%         geomSurfaceArea_strings = blank_array;
%         geomSurfaceArea_strings(1) = string(geomSurfaceArea_data);  % add charge per phase to array
%     end
%     chargeInj_strings = blank_array;
%     chargeInj_strings(1) = string(chargeInj_data);              % add charge injection to array
%     maxPotential_strings = blank_array;
%     maxPotential_strings(1) = string(maxPotential_data);        % add max potential to array
%     voltageDrive_strings = blank_array;
%     voltageDrive_strings(1) = string(voltageDrive_data);        % add driving voltage to array
%     dateTime_strings = blank_array;
%     dateTime_strings(1) = dateTime_data;                        % add date and time to array
%     status_strings = blank_array;
%     status_strings(1) = status_data;
%     
%     % Table
%     if all(maxPotential_data < 0)
%         maxPotentialTitle = 'Emc (V)';
%     else
%         maxPotentialTitle = 'Ema (V)';
%     end
%     
%     if hasArea
%         tableHeading = {...     % heading
%             'Time (us)',...     % time
%             'Voltage (V)',...   % voltage transient
%             'Current (uA)',...  % current stimulation
%             'Qph (nC/ph)',...   % charge per phase
%             'GSA (um2)',...     % geometric surface area
%             'Qinj (uC/cm2)',... % charge injection
%             maxPotentialTitle,...       % max cathodal potential
%             'Vdrive (V)',...    % driving voltage
%             'Completed On',...  % date and time
%             'Status'};
%     else
%         tableHeading = {...     % heading
%             'Time (us)',...     % time
%             'Voltage (V)',...   % voltage transient
%             'Current (uA)',...  % current stimulation
%             'Qph (nC/ph)',...   % charge per phase
%             'Qinj (uC/cm2)',... % charge injection
%             maxPotentialTitle,...       % max cathodal potential
%             'Vdrive (V)',...    % driving voltage
%             'Completed On',...  % date and time
%             'Status'};
%     end
%     
%     if hasArea
%         tableData = table(...               % table
%             time_data,...                   % time
%             voltage_data,...                % voltage transient
%             current_data,...                % current stimulation
%             chargePerPh_strings,...         % Qph
%             geomSurfaceArea_strings,...     % GSA
%             chargeInj_strings,...           % Qinj
%             maxPotential_strings,...        % Emc
%             voltageDrive_strings,...        % Vdrive
%             dateTime_strings,...            % DateTime
%             status_strings,...
%             'VariableNames',tableHeading);
%     else
%         tableData = table(...               % table
%             time_data,...                   % time
%             voltage_data,...                % voltage transient
%             current_data,...                % current stimulation
%             chargePerPh_strings,...         % Qph
%             chargeInj_strings,...           % Qinj
%             maxPotential_strings,...        % Emc
%             voltageDrive_strings,...        % Vdrive
%             dateTime_strings,...            % DateTime
%             status_strings,...
%             'VariableNames',tableHeading);
%     end
%     
%     % Make filename for .csv file
%     %     filename_csv_char = sprintf('%s_Ch%02d.csv',filename_char,channelNum);  % filename for .csv file (char)
%     %     filename_csv = convertCharsToStrings(filename_csv_char);             	% filename string for .csv file (string)
%     %     filename_csv_save = fullfile(savePath,filename_csv);               	% save path for .csv file
%     
%     % Save spreadsheets
%     %     writetable(tableData,filename_csv_save);    % save .csv file
%     %     fprintf('%s\n',filename_csv);   % .csv file saved
%     channelNum_char = sprintf('Channel %d',channelNum);     % channel number (char)
%     channelNum_str = convertCharsToStrings(channelNum_char);% channel number (string)
%     writetable(tableData,filename_xlsx_save,'Sheet',channelNum_str);    % save sheet to .xlsx file
%     channelList_alloc = [channelList,{channelNum_char}];
%     channelList = channelList_alloc;
%     chargePhaseList_alloc = [chargePhaseList,chargePhase_data];
%     chargePhaseList = chargePhaseList_alloc;
%     chargeInjList_alloc = [chargeInjList,chargeInj_data];
%     chargeInjList = chargeInjList_alloc;
%     maxPotentialList_alloc = [maxPotentialList,maxPotential_data];
%     maxPotentialList = maxPotentialList_alloc;
%     drivingVoltageList_alloc = [drivingVoltageList,voltageDrive_data];
%     drivingVoltageList = drivingVoltageList_alloc;
%     fprintf('%d...',channelNum);
% end
% sheetName = 'Voltage Measurements';
% headings = tableHeading(4:7);
% data_array = [chargePhaseList;chargeInjList;maxPotentialList;drivingVoltageList];
% dataTable = array2table(...
%     data_array,...
%     'VariableNames',channelList,...
%     'RowNames',headings);
% % data = array2table(...
% %     chargeInjList,...
% %     maxPotentialList,...
% %     drivingVoltageList,...
% %     'VariableNames',channelList,...
% %     'RowNames',headings);
% writetable(dataTable,filename_xlsx_save,'Sheet',sheetName,'WriteRowNames',true);    % save sheet to .xlsx file
% fprintf('%s\n',filename_xlsx);	% .xlsx file saved
% % fprintf('All files saved.\n');% all files saved

end